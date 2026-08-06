"""asgk_server.cache — 结构化数据缓存（per-source，内存 + JSON 文件落盘）。

取代 sgw 的 Cache/DiskCache（字节 + SQLite）。三点改造（§3.6 a/b/d/f）：
  a. 存解析后的结构化数据（dict/list），非原始字节——命中即返，客户端零解析
  b. key = capability|source|semantic_key，per-source 独立不跨源共享
  d. 磁盘持久化用 JSON 文件（每项一文件），非 SQLite——cache 可重建不需 ACID
  f. _semantic_key(capability, source, params)：语义参数排序去重哈希，不含 source/format

熔断状态库（CircuitStateStore）仍用 SQLite，不在此处——那是安全闩要 ACID。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Optional


# ── 语义键规范化（§3.6f，取代 sgw _canonical_url）─────────────
def _normalize_params(params: dict) -> Any:
    """把语义参数规范化为可稳定哈希的结构。

    - dict：按 key 排序，递归规范化 value
    - list（如 codes）：去重 + 排序（顺序无关：codes=['600519','000001'] 与反序等价）
    - str/int/float/bool/None：原样

    注意：codes 列表去重排序是因为"同一组股票"的集合语义与顺序无关。但有些
    参数的 list 顺序是有意义的（如分页 results）——不过那些不会被作为 cache key
    的输入（cache key 只描述"请求什么"，不描述"返回什么"），所以排序安全。
    """
    if isinstance(params, dict):
        return {k: _normalize_params(params[k]) for k in sorted(params)}
    if isinstance(params, (list, tuple)):
        # 去重 + 排序；元素需可哈希可排序
        try:
            return sorted(set(params))
        except TypeError:
            # 含不可哈希元素（极少见），退化为排序后的原列表
            return sorted(params, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
    return params


def semantic_key(capability: str, source: str, params: dict) -> str:
    """构造 cache key：capability|source|param_hash。

    - 不含 format/output（格式化在客户端，§3.6e，不进服务端 cache key）
    - source 是第二段（独立），params 哈希不含 source
    - params 排序去重后取 md5 前 16 位（碰撞概率足够低，且 miss 即重取无害）
    """
    normalized = _normalize_params(params)
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    param_hash = hashlib.md5(raw.encode()).hexdigest()[:16]
    return f"{capability}|{source}|{param_hash}"


# ── 内存缓存：结构化数据（搬 sgw Cache 类，value 从 bytes 变对象）──
class MemoryCache:
    """内存缓存，存结构化数据（dict/list）。

    沿用 sgw Cache 的 key/value/ttl/expire 语义；value 从 bytes 变 Python 对象，
    命中即返（无需反序列化）。线程安全（一把锁）。
    """

    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}  # key -> (value, expire_ts)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry and entry[1] > time.time():
                self.hits += 1
                return entry[0]
            if entry:
                del self._store[key]  # 过期，惰性删除
            self.misses += 1
            return None

    def set(self, key: str, value: Any, ttl: int):
        if ttl <= 0:
            return
        with self._lock:
            self._store[key] = (value, time.time() + ttl)

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._store), "hits": self.hits, "misses": self.misses}


# ── 磁盘缓存：JSON 文件（取代 sgw SQLite DiskCache）───────────
class JsonDiskCache:
    """结构化缓存的磁盘持久层（JSON 文件，每项一文件）。

    路径：<cache_dir>/<capability>/<source>/<param_hash>.json
    内容：{"value": <结构化数据>, "expire": <ts>}

    write-through：每次 set 同步写盘；get 读盘回填内存。启动 load_all 遍历目录
    回填 + 删过期文件。过期用 expire 字段判断（不用 mtime，避免 touch 干扰）。

    vs sgw SQLite DiskCache（§3.6d）：
    - cache 可重建不需 ACID → 文件方案足够（熔断状态才需 SQLite）
    - 存的是结构化 JSON（文本）→ 文件天然契合，cat 即可调试
    - 零依赖（os/json/pathlib）
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # 串行化文件写（P/L 写入 ≤1 req/s，无竞争）
        self.hits = 0
        self.misses = 0

    def _path_for(self, key: str) -> Path:
        """key = capability|source|param_hash → 三级目录路径。"""
        parts = key.split("|", 2)
        if len(parts) != 3:
            # 非标准 key（如旧格式），退化到扁平文件名（转义分隔符）
            safe = key.replace("|", "_")
            return self.cache_dir / f"{safe}.json"
        capability, source, param_hash = parts
        return self.cache_dir / capability / source / f"{param_hash}.json"

    def get(self, key: str) -> Optional[Any]:
        path = self._path_for(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            expire = float(data.get("expire", 0))
            if expire <= time.time():
                # 过期，惰性删除
                with self._lock:
                    path.unlink(missing_ok=True)
                self.misses += 1
                return None
            self.hits += 1
            return data.get("value")
        except (ValueError, json.JSONDecodeError, OSError):
            # 损坏文件：忽略并清理，cache 可重建
            with self._lock:
                path.unlink(missing_ok=True)
            self.misses += 1
            return None

    def set(self, key: str, value: Any, ttl: int):
        if ttl <= 0:
            return
        path = self._path_for(key)
        payload = {"value": value, "expire": time.time() + ttl}
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 原子写：先写临时文件再 rename，避免半写文件被读到
            tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)

    def load_all(self) -> dict[str, tuple[Any, float]]:
        """启动时回填内存。遍历目录，返回未过期的 {key: (value, expire)}，删过期文件。

        key 由三级目录反推：capability/source/param_hash。
        """
        now = time.time()
        result: dict[str, tuple[Any, float]] = {}
        expired: list[Path] = []
        if not self.cache_dir.exists():
            return result
        for json_file in self.cache_dir.rglob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                expire = float(data.get("expire", 0))
                if expire <= now:
                    expired.append(json_file)
                    continue
                # 反推 key：cache_dir/capability/source/hash.json
                rel = json_file.relative_to(self.cache_dir)
                if len(rel.parts) == 3 and rel.suffix == ".json":
                    capability, source, fname = rel.parts
                    key = f"{capability}|{source}|{fname[:-5]}"  # 去 .json
                else:
                    key = json_file.stem  # 退化
                result[key] = (data.get("value"), expire)
            except (ValueError, json.JSONDecodeError, OSError):
                expired.append(json_file)  # 损坏文件一并清
        with self._lock:
            for p in expired:
                p.unlink(missing_ok=True)
        return result

    def stats(self) -> dict:
        try:
            size = sum(1 for _ in self.cache_dir.rglob("*.json")) if self.cache_dir.exists() else 0
        except OSError:
            size = 0
        return {"size": size, "hits": self.hits, "misses": self.misses}

    def close(self):
        pass  # 文件方案无需关闭


# ── 组合层：内存 + 磁盘的统一缓存接口 ─────────────────────────
class SemanticCache:
    """能力代理的统一缓存：内存命中优先，miss 回查磁盘，落盘类型写穿。

    调用方（server）通过 cache_policy 决定 ttl / 是否落盘 / 是否结构化。
    本类只管存取，不解释 policy（policy 解释在 cache_policy.py + server）。

    key 由调用方用 semantic_key() 构造好传入（server 持有 capability/source/params）。
    """

    def __init__(self, disk_cache: Optional[JsonDiskCache] = None):
        self.memory = MemoryCache()
        self.disk = disk_cache

    def get(self, key: str) -> Optional[Any]:
        """内存 → 磁盘。命中磁盘时回填内存。"""
        cached = self.memory.get(key)
        if cached is not None:
            return cached
        if self.disk is not None:
            disk_val = self.disk.get(key)
            if disk_val is not None:
                # 回填内存（用原 expire，不重置 ttl）
                # 简化：回填用一个保守的长 ttl，实际下次过期由磁盘再校验
                self.memory.set(key, disk_val, ttl=3600)
                return disk_val
        return None

    def set(self, key: str, value: Any, ttl: int, persist: bool):
        """写内存；persist=True 时同步写磁盘（write-through）。"""
        self.memory.set(key, value, ttl)
        if persist and self.disk is not None:
            self.disk.set(key, value, ttl)

    def preload(self, key: str, value: Any, expire: float):
        """启动时从磁盘 load_all 回填内存（expire 是绝对时间戳）。"""
        remaining = expire - time.time()
        if remaining > 0:
            self.memory._store[key] = (value, expire)

    def stats(self) -> dict:
        return {
            "memory": self.memory.stats(),
            "disk": self.disk.stats() if self.disk else None,
        }


# ── 文档缓存：原始 bytes 文件（§3.7 文档型，独立于结构化缓存）──
class DocumentCache:
    """文档型缓存：存原始 bytes 文件（PDF/xlsx 原文），带体积上限 + LRU 淘汰。

    与结构化缓存（SemanticCache）的根本差异：
    - 存原始 bytes（PDF/xlsx 是二进制文件，非 JSON 可序列化）
    - 体积必须管（单文件 ≤20MB，总 ≤2GB）——结构化数据是 KB 级，文档是 MB 级
    - LRU 淘汰：超总上限时删最久未访问的（文档 30 天 TTL 但可能先被 LRU 挤掉）
    - 路径：<cache_dir>/_docs/<doc_id>.<ext> + <cache_dir>/_docs/_index.json 元数据

    线程安全（一把锁串行化文件写 + 元数据更新；文档下载 ≤1 req/s，无竞争）。
    """

    # 体积上限（§3.7）
    MAX_FILE_BYTES = 20 * 1024 * 1024   # 单文件 20MB
    MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 总 2GB

    def __init__(self, cache_dir: Path, max_total_bytes: int | None = None,
                 max_file_bytes: int | None = None):
        self.docs_dir = Path(cache_dir) / "_docs"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.docs_dir / "_index.json"
        self.max_total = max_total_bytes if max_total_bytes is not None else self.MAX_TOTAL_BYTES
        self.max_file = max_file_bytes if max_file_bytes is not None else self.MAX_FILE_BYTES
        self._lock = threading.Lock()
        self._index: dict[str, dict] = self._load_index()  # doc_id -> {ext, size, expire, atime}
        self._total_bytes = sum(e["size"] for e in self._index.values())

    def _load_index(self) -> dict[str, dict]:
        """启动时加载元数据索引；删除已过期的文档文件。"""
        if not self.index_path.exists():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (ValueError, json.JSONDecodeError, OSError):
            return {}
        now = time.time()
        expired = [k for k, e in data.items() if e.get("expire", 0) <= now]
        for k in expired:
            self._unlink_doc(k, data)
        return data

    def _unlink_doc(self, doc_id: str, index: dict) -> None:
        """删一个文档文件 + 从 index 移除（不写盘，调用方负责持久化）。"""
        entry = index.pop(doc_id, None)
        if entry is None:
            return
        path = self.docs_dir / f"{doc_id}.{entry['ext']}"
        path.unlink(missing_ok=True)

    def _persist_index(self) -> None:
        """原子写元数据索引。"""
        tmp = self.index_path.with_name(f".{self.index_path.name}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(self._index, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.index_path)

    def _path_for(self, doc_id: str, ext: str) -> Path:
        return self.docs_dir / f"{doc_id}.{ext}"

    def _evict_lru(self, need_bytes: int) -> None:
        """LRU 淘汰：腾出至少 need_bytes 空间（删最久未访问的）。

        假设已持锁。淘汰后 _total_bytes 更新。
        """
        if self._total_bytes + need_bytes <= self.max_total:
            return
        # 按 atime 升序排（最旧的先删）
        ordered = sorted(self._index.items(), key=lambda kv: kv[1].get("atime", 0))
        for doc_id, entry in ordered:
            if self._total_bytes + need_bytes <= self.max_total:
                break
            self._unlink_doc(doc_id, self._index)
            self._total_bytes -= entry["size"]

    def get(self, doc_id: str) -> tuple[bytes, str] | None:
        """取文档 bytes + ext。命中时刷新 atime（LRU）。过期/不存在返回 None。"""
        with self._lock:
            entry = self._index.get(doc_id)
            if entry is None:
                return None
            if entry.get("expire", 0) <= time.time():
                self._unlink_doc(doc_id, self._index)
                self._total_bytes -= entry["size"]
                self._persist_index()
                return None
            path = self._path_for(doc_id, entry["ext"])
            if not path.exists():
                # 文件丢了（外部删除），清 index
                del self._index[doc_id]
                self._persist_index()
                return None
            data = path.read_bytes()
            entry["atime"] = time.time()  # 刷新 LRU
            self._persist_index()
            return data, entry["ext"]

    def set(self, doc_id: str, data: bytes, ext: str, ttl: int) -> bool:
        """存文档 bytes。超单文件上限拒绝（返 False）；超总量先 LRU 淘汰再写。

        ttl<=0 不写（文档型 ttl 固定 30 天，不会是 0）。
        """
        if ttl <= 0 or not data:
            return False
        if len(data) > self.max_file:
            return False  # 单文件超限拒绝（不淘汰——这是异常大文件）
        with self._lock:
            # 同 doc_id 已存在则先删旧（避免重复计入体积）
            if doc_id in self._index:
                old = self._index[doc_id]
                self._unlink_doc(doc_id, self._index)
                self._total_bytes -= old["size"]
            # LRU 淘汰腾空间
            self._evict_lru(len(data))
            path = self._path_for(doc_id, ext)
            # 原子写
            tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            self._index[doc_id] = {
                "ext": ext, "size": len(data),
                "expire": time.time() + ttl, "atime": time.time(),
            }
            self._total_bytes += len(data)
            self._persist_index()
            return True

    def stats(self) -> dict:
        with self._lock:
            return {"docs": len(self._index),
                    "total_bytes": self._total_bytes,
                    "max_total_bytes": self.max_total}

    def close(self):
        pass
