#!/usr/bin/env python3
"""票 #21:查询内语义重排打分器(本地 bge-small-zh-v1.5 ONNX,零上游零 API 成本)。

CONTEXT 词条「查询内语义重排」的落地实现:每次 ask 对拉回的候选做本地轻量语义打分,
只作用于 topK 深读选择(决定哪些文档被深读),不改召回集合、不改展示排序。
无预囤、无向量落盘、离线缓存不向量化(向量库已降级待墙,ADR-0005)。

模型:Xenova/bge-small-zh-v1.5 的 onnx/model.onnx + tokenizer.json
     (hf-mirror.com 直连下载,落 ~/.lingeebuild/models/bge-small-zh-v1.5/,~96MB)。
用法要点(bge 系模型约定,做错打分即噪声):
  - 查询侧加指令前缀「为这个句子生成表示以用于检索相关文章:」;
  - 文档侧不加前缀;
  - CLS 池化 + L2 归一,余弦 = 内积。

开关与降级(KSEARCH_RERANK_SEMANTIC,默认 off):
  模型文件缺失 / onnxruntime 或 tokenizers 未装 / 单次打分异常 / 超时(>5s)
  一律静默降级为原排序,由调用方在返回体附 semanticRerank{enabled,reason} 绝不影响回答可用性。
"""
import os
import threading
import time

# 查询侧指令前缀(bge 系官方约定;文档侧不加)
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章:"
MAX_LEN = 512            # bge-small-zh 上限 512 token
MAX_CANDIDATES = 50      # 打分只作用前 50 条(融合排名靠前者),其余保持原序
DEFAULT_TIMEOUT_S = 5.0  # 单次打分超时,超限降级

MODEL_DIR = os.environ.get(
    "KSEARCH_RERANK_MODEL_DIR",
    os.path.join(os.path.expanduser("~"), ".lingeebuild", "models", "bge-small-zh-v1.5"))

# 开关默认 OFF(KSEARCH_RERANK_SEMANTIC=off);评测过门后由用户拍板转默认 ON
ENABLED_DEFAULT = os.environ.get("KSEARCH_RERANK_SEMANTIC", "off").lower() in ("1", "true", "on", "yes")

_st = {"loaded": False, "reason": "not-loaded", "session": None, "tok": None, "inputs": None,
       "output": None, "threads": None}
_lock = threading.Lock()


def _log(err_fn, *a):
    try:
        err_fn("[semantic-rerank] " + " ".join(str(x) for x in a))
    except Exception:
        pass


def ensure_loaded(err_fn=lambda m: None):
    """懒加载 ONNX 会话与分词器(进程内仅一次,失败固化 reason 供降级报告)。"""
    if _st["loaded"] or _st["reason"] in ("loaded",):
        return True
    with _lock:
        if _st["loaded"]:
            return True
        try:
            onnx_path = os.path.join(MODEL_DIR, "onnx-model.onnx")
            tok_path = os.path.join(MODEL_DIR, "tokenizer.json")
            missing = [p for p in (onnx_path, tok_path) if not os.path.isfile(p)]
            if missing:
                _st["reason"] = "model files missing: %s" % ", ".join(os.path.basename(p) for p in missing)
                return False
            import onnxruntime as ort           # 未装 → 降级
            from tokenizers import Tokenizer    # 未装 → 降级
            tok = Tokenizer.from_file(tok_path)
            tok.enable_truncation(max_length=MAX_LEN)
            tok.enable_padding(pad_id=0)        # 动态 pad 到批内最长
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2         # small 模型,限线程防抢主服务 CPU
            sess = ort.InferenceSession(onnx_path, sess_options=so, providers=["CPUExecutionProvider"])
            _st["session"] = sess
            _st["tok"] = tok
            _st["inputs"] = [i.name for i in sess.get_inputs()]
            outs = [o.name for o in sess.get_outputs()]
            _st["output"] = "last_hidden_state" if "last_hidden_state" in outs else outs[0]
            _st["loaded"] = True
            _st["reason"] = "loaded"
            _log(err_fn, "model loaded:", MODEL_DIR, "| inputs", _st["inputs"], "| output", _st["output"])
            return True
        except Exception as e:
            _st["reason"] = "load fail: %s" % str(e)[:150]
            _log(err_fn, _st["reason"])
            return False


def status():
    """/health 用:开关 + 模型加载状态(不触发重试风暴,失败 reason 固化可见)。"""
    return {"enabled": ENABLED_DEFAULT, "modelLoaded": bool(_st["loaded"]),
            "reason": _st["reason"], "modelDir": MODEL_DIR, "queryPrefix": QUERY_PREFIX,
            "maxCandidates": MAX_CANDIDATES, "timeoutMs": int(DEFAULT_TIMEOUT_S * 1000)}


def _embed(sess, tok, input_names, output_name, texts):
    encs = tok.encode_batch(texts)
    ids = [e.ids for e in encs]
    att = [e.attention_mask for e in encs]
    maxl = max(len(x) for x in ids)
    ids = [x + [0] * (maxl - len(x)) for x in ids]
    att = [x + [0] * (maxl - len(x)) for x in att]

    def to_arr(m):
        import numpy as np
        return np.array(m, dtype=np.int64)

    feed = {"input_ids": to_arr(ids), "attention_mask": to_arr(att)}
    if "token_type_ids" in input_names:
        feed["token_type_ids"] = to_arr([[0] * maxl for _ in ids])
    feed = {k: v for k, v in feed.items() if k in input_names}
    out = sess.run([output_name], feed)[0]          # (batch, seq, hidden)
    import numpy as np
    cls = out[:, 0, :]                              # bge 约定:CLS 池化
    norm = np.linalg.norm(cls, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return cls / norm


def _doc_text(item):
    """候选侧文本:深读前只有 title+snippet 可用(不改召回集合,只判相关方向)。"""
    return (" ".join(p for p in (str(item.get("title") or ""), str(item.get("snippet") or "")) if p)
            .strip())[:1200]


def score_candidates(query, candidates, timeout_s=DEFAULT_TIMEOUT_S, err_fn=lambda m: None):
    """对候选按 query-doc 余弦相似度打分。返回 info dict(含每候选分数,按 candidates 原序):
    {ok, reason, elapsedMs, scores:[{key,score}], model}。异常/超时一律 ok=False 由调用方降级。"""
    info = {"ok": False, "reason": "not-attempted", "elapsedMs": None,
            "scores": [], "model": "bge-small-zh-v1.5"}
    if not query or not candidates:
        info["reason"] = "empty query or candidates"
        return info
    if not ensure_loaded(err_fn):
        info["reason"] = _st["reason"]
        return info
    t0 = time.time()
    box = {}

    def _work():
        try:
            texts = [QUERY_PREFIX + str(query)] + [_doc_text(it) for it in candidates]
            emb = _embed(_st["session"], _st["tok"], _st["inputs"], _st["output"], texts)
            box["scores"] = (emb[1:] @ emb[0]).tolist()  # 余弦(L2 归一后内积即余弦)
            box["err"] = None
        except Exception as e:
            box["err"] = str(e)[:150]

    th = threading.Thread(target=_work, daemon=True)
    th.start()
    th.join(timeout=max(timeout_s, 0.1))
    info["elapsedMs"] = round((time.time() - t0) * 1000, 1)
    if th.is_alive():
        info["reason"] = "timeout >%.0fs" % timeout_s
        return info
    if box.get("err"):
        info["reason"] = "inference fail: %s" % box["err"]
        return info
    sc = box.get("scores")
    if sc is None or len(sc) != len(candidates):
        info["reason"] = "score count mismatch"
        return info
    info["ok"] = True
    info["reason"] = "ok"
    for it, s in zip(candidates, sc):
        info["scores"].append({"key": _item_key(it), "score": round(float(s), 4)})
    return info


def _item_key(it):
    return ("answer:" + str(it.get("questionId"))) if it.get("type") == "answer" and it.get("questionId") \
        else "%s:%s" % (it.get("type", "?"), it.get("id"))


def rerank_candidates(query, ranked, err_fn=lambda m: None, timeout_s=DEFAULT_TIMEOUT_S):
    """ask 深读 topK 选择前的语义重排入口。返回 (新 ranked 序列, semanticRerank 字段)。
    - 只重排前 MAX_CANDIDATES 条,其余保持原序追加;
    - 打分失败/超时 → 原序列原样返回(ok=False,reason 可见),绝不影响回答可用性;
    - 展示排序由调用方按三层路由规则另行重排,本函数只决定深读选择顺序。"""
    enabled = ENABLED_DEFAULT
    info = {"enabled": enabled, "model": "bge-small-zh-v1.5", "candidates": 0,
            "reordered": False, "reason": "switch off" if not enabled else "not-attempted",
            "elapsedMs": None, "scores": []}
    if not enabled:
        return ranked, info
    if len(ranked) < 2:
        info["reason"] = "single candidate"
        return ranked, info
    head = ranked[:MAX_CANDIDATES]
    tail = ranked[MAX_CANDIDATES:]
    info["candidates"] = len(head)
    sc_info = score_candidates(query, head, timeout_s=timeout_s, err_fn=err_fn)
    info["elapsedMs"] = sc_info["elapsedMs"]
    if not sc_info["ok"]:
        info["reason"] = sc_info["reason"]
        _log(err_fn, "degrade:", info["reason"])
        return ranked, info
    info["reason"] = "ok"
    info["scores"] = sc_info["scores"]
    ordered = [it for _, it in
               sorted(zip(sc_info["scores"], head), key=lambda t: -t[0]["score"])] + tail
    info["reordered"] = True
    _log(err_fn, "reranked %d candidates in %.0fms; top scores:" % (len(head), info["elapsedMs"]),
         ", ".join("%s=%.4f" % (s["key"], s["score"]) for s in sc_info["scores"][:10]))
    return ordered, info
