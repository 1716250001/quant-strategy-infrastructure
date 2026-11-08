# -*- coding: utf-8 -*-
"""F3/R10 断点时间戳：旧文件兼容 ＋ 新键写入（CC-FIX-2R Step3）

注意：AltCheckpoint 受 `alt.config.assert_writable` 守卫，只能写授权根
（全量数据 alt_data、.temp/alt_run），故临时文件落在后者。
"""
import json
import shutil
import uuid

import pytest

from alt.backfill import AltCheckpoint
from fetch.base import Checkpoint

ALT_TMP_ROOT = r"D:\量化策略\.temp\alt_run"


@pytest.fixture()
def alt_dir():
    d = f"{ALT_TMP_ROOT}\\_ckpt_test_{uuid.uuid4().hex[:8]}"
    import os
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_old_checkpoint_file_without_timestamps(tmp_path, alt_dir):
    """旧文件（无 last_fetch/last_attempt）照常读，completed 语义不变。"""
    p = tmp_path / ".checkpoint.json"
    p.write_text(json.dumps({"daily": {"completed": ["600519.SH"]}}), encoding="utf-8")
    cp = Checkpoint(data_root=str(tmp_path))
    assert cp.is_done("daily", "600519.SH")
    assert cp.get_completed_count("daily") == 1
    assert "last_fetch" not in cp.data["daily"]

    ap = alt_dir + r"\.alt_checkpoint.json"
    with open(ap, "w", encoding="utf-8") as fh:
        json.dump({"ebs_lg": {"done": ["__all__"]}}, fh)
    ac = AltCheckpoint(path=ap)
    assert ac.is_done("ebs_lg")           # done:["__all__"] 跳过语义不动
    assert "last_attempt" not in ac.data["ebs_lg"]


def test_new_timestamp_keys_written(tmp_path, alt_dir):
    """新键落盘：market last_fetch、alt last_attempt；旧键无损。"""
    cp = Checkpoint(data_root=str(tmp_path))
    cp.mark_done("daily", "600519.SH")
    cp.mark_fetched("daily")
    cp.save()
    d = json.loads((tmp_path / ".checkpoint.json").read_text(encoding="utf-8"))
    assert d["daily"]["completed"] == ["600519.SH"]
    assert "T" in d["daily"]["last_fetch"]

    ap = alt_dir + r"\.alt_checkpoint.json"
    ac = AltCheckpoint(path=ap)
    ac.mark_done("ebs_lg", "__all__")
    ac.mark_attempt("ebs_lg")
    d2 = json.loads(open(ap, encoding="utf-8").read())
    assert d2["ebs_lg"]["done"] == ["__all__"]
    assert "T" in d2["ebs_lg"]["last_attempt"]
    assert "updated" in d2["ebs_lg"]
