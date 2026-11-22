# -*- coding: utf-8 -*-
"""
common/concurrency.py — 跨进程锁与原子替换重试（D3，2026-11-21）
================================================================
从 `fetch/base.py` 提升出来的通用设施，供断点写（Checkpoint）与
parquet 写（common/parquet_store）共用：

  - FileLock / file_lock: `O_CREAT|O_EXCL` 跨进程互斥（可配超时）
  - LockTimeout: 超时异常，信息含持锁 PID 与"人工删除"提示
  - replace_with_retry: `os.replace` 遇 WinError 5/32 重试
  - pid_tmp_path / cleanup_pid_tmp: 本进程 tmp 命名与清理

设计边界
--------
1. **不自动删除残锁**。锁文件只写 PID + 时间戳；持锁进程是否还活着无法
   可靠判断（PID 复用），自动删锁会让两个写者同时进入。超时即抛
   `LockTimeout`，由人工确认 PID 后删除。
2. **只清理本进程 tmp**。`cleanup_pid_tmp` 只删 `{path}.{本进程PID}.*.tmp`，
   不碰其他进程的临时文件 —— 否则会把正在写的 tmp 删掉。
3. `os.replace` 重试仅覆盖 Windows 共享冲突（WinError 32/5）；其他 OSError
   立即上抛，避免把磁盘满/路径错误伪装成瞬时冲突。
"""

import os
import time
import uuid
from datetime import datetime

DEFAULT_LOCK_TIMEOUT = 30.0  # 秒；03 §五 D3 风险表口径
DEFAULT_RETRY_MS = 15  # 秒；轮询间隔
REPLACE_RETRIES = 40  # 40 × 15ms ≈ 0.6s

# Windows: ERROR_ACCESS_DENIED / ERROR_SHARING_VIOLATION
_WIN_SHARING_ERRORS = (5, 32)


class LockTimeout(RuntimeError):  # noqa: N818 - 03 §五 D3 固定名称
    """获取跨进程文件锁超时。

    属性：
        path        锁文件路径
        timeout     等待秒数
        holder_pid  从锁文件解析出的 PID（解析不到为 None）
        holder_text 锁文件原文（可能为 None）
    """

    def __init__(self, path, timeout, holder_text=None):
        self.path = path
        self.timeout = timeout
        self.holder_text = holder_text
        self.holder_pid = _parse_pid(holder_text)
        holder = f"进程 {self.holder_pid}" if self.holder_pid is not None else "未知进程"
        detail = f"（锁文件内容：{holder_text}）" if holder_text else ""
        super().__init__(
            f"获取锁超时：{path} 被 {holder} 持有，已等待 {timeout:g}s{detail}；"
            f"请确认该 PID 不存在后**人工删除**锁文件再重试"
        )


def _parse_pid(text):
    """从 `"<pid> <timestamp>"` 解析 PID；失败返回 None。"""
    if not text:
        return None
    head = str(text).strip().split(None, 1)[0]
    try:
        return int(head)
    except (TypeError, ValueError):
        return None


def _read_holder(path):
    """读取锁文件内容（只用于报错，不求成功）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(200).strip()
    except OSError:
        return None


class FileLock:
    """跨进程互斥锁：`O_CREAT|O_EXCL` 建锁文件，超时抛 `LockTimeout`。

    ⚠ 为什么需要（2026-11-16 index_weight 校准批 P0 实证）：
      `run_backfill` 的两处写入都**没有跨进程保护**（现有 `threading.Lock`
      只在单进程内有效）：
        ① 断点写 `cp.save()` = 全量覆盖 `json.dump(self.data)`
        ② 数据写 `merge_append` = 全文件读改写
      两个 backfill 实例并跑时，后写者读到的是旧文件 ⇒ 前者整批数据被
      静默覆盖丢失；而断点两边各自 mark_done，后 save 时已看见前者 ⇒
      键数翻倍。最终表现为「断点说完成、数据没有」，且 `--dry-run`
      报「待跑=0」＝**假绿**。

    用法：
        with file_lock(path, timeout=30):
            ...
    或：
        lock = FileLock(path, timeout=30)
        lock.acquire()
        try:
            ...
        finally:
            lock.release()
    """

    def __init__(self, path, timeout=DEFAULT_LOCK_TIMEOUT, retry_ms=DEFAULT_RETRY_MS):
        self.path = path
        self.timeout = float(timeout)
        self.retry_ms = max(int(retry_ms), 1)
        self.held = False

    def acquire(self):
        if self.held:
            return self
        deadline = time.monotonic() + max(self.timeout, 0.0)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = _read_holder(self.path)
                if time.monotonic() >= deadline:
                    raise LockTimeout(self.path, self.timeout, holder) from None
                time.sleep(self.retry_ms / 1000.0)
                continue
            try:
                os.write(
                    fd, f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}".encode()
                )
            finally:
                os.close(fd)
            self.held = True
            return self

    def release(self):
        if not self.held:
            return
        # Windows：另一个线程/进程的 `_read_holder` 可能正打开锁文件读取
        # （CRT 默认不带 FILE_SHARE_DELETE），此时 `os.remove` 抛 WinError 32。
        # 原实现吞掉异常并置 held=False ⇒ 锁文件永久残留、其余写者全部超时。
        # 这里短重试到读者关闭句柄（通常 <1ms）。
        for i in range(40):
            try:
                os.remove(self.path)
                break
            except FileNotFoundError:
                break
            except OSError:
                if i == 39:
                    print(f"[WARN] 锁文件删除失败（需人工确认）: {self.path}")
                    break
                time.sleep(0.005)
        self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


def file_lock(path, timeout=DEFAULT_LOCK_TIMEOUT, retry_ms=DEFAULT_RETRY_MS):
    """创建文件锁（未获取；配合 `with` 使用）。"""
    return FileLock(path, timeout=timeout, retry_ms=retry_ms)


def _is_sharing_violation(exc):
    """是否为 Windows 文件共享冲突（可重试）。"""
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "winerror", None) in _WIN_SHARING_ERRORS:
        return True
    return False


def replace_with_retry(src, dst, retries=REPLACE_RETRIES, retry_ms=DEFAULT_RETRY_MS):
    """`os.replace(src, dst)`，Windows 共享冲突时短重试。

    为什么：数据/断点写完后立刻 replace 时，杀毒、索引器或另一个读者
    可能短暂持有目标句柄 ⇒ `PermissionError [WinError 32]`。原实现直接
    抛出会把"一次瞬时冲突"变成"整天断点没保存"。
    """
    attempts = max(int(retries), 1)
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if not _is_sharing_violation(exc) or i == attempts - 1:
                raise
            time.sleep(max(int(retry_ms), 1) / 1000.0)


def pid_tmp_path(path):
    """本进程专属临时文件名：`{path}.{pid}.{uuid8}.tmp`。

    固定 `.tmp` 名会让两个进程互相覆盖对方的半成品；带 PID + uuid
    后每个写者独享自己的临时文件。
    """
    return f"{path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"


def cleanup_pid_tmp(path):
    """删除本进程遗留的 `{path}.{pid}.*.tmp`（不碰他人 tmp）。"""
    directory = os.path.dirname(os.path.abspath(path))
    base = os.path.basename(path)
    prefix = f"{base}.{os.getpid()}."
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if not (name.startswith(prefix) and name.endswith(".tmp")):
            continue
        try:
            os.remove(os.path.join(directory, name))
            removed += 1
        except OSError:
            pass
    return removed
