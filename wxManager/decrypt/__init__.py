#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
@Time        : 2025/1/10 2:34
@Author      : SiYuan
@Email       : 863909694@qq.com
@File        : wxManager-__init__.py.py
@Description : WeChat process discovery helpers
"""
from typing import List

import psutil

from wxManager.decrypt.wx_info_v3 import dump_wechat_info_v3
from wxManager.decrypt.wx_info_v4 import dump_wechat_info_v4
from wxManager.decrypt.common import WeChatInfo


def get_info_v4_diagnostics() -> tuple[List[WeChatInfo], list[str]]:
    """Discover WeChat 4.x accounts while isolating failures per Weixin.exe process.

    Newer WeChat releases can create several Weixin.exe processes with different
    access characteristics. A failure while inspecting one child process should
    not abort discovery of the main process. The returned diagnostics intentionally
    report only whether a key was found; key material is never included.
    """
    result_v4: list[WeChatInfo] = []
    diagnostics: list[str] = []
    candidate_count = 0

    for process in psutil.process_iter(["name", "exe", "pid"]):
        pid = process.info.get("pid") or getattr(process, "pid", 0)
        try:
            name = process.info.get("name") or process.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
            diagnostics.append(f"PID {pid or '?'}: process metadata unavailable ({type(exc).__name__}).")
            continue
        except Exception as exc:
            diagnostics.append(f"PID {pid or '?'}: process metadata error ({type(exc).__name__}).")
            continue

        if (name or "").lower() != "weixin.exe":
            continue
        candidate_count += 1

        try:
            modules = process.memory_maps(grouped=False)
            has_weixin_dll = any(module.path and "weixin.dll" in module.path.lower() for module in modules)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
            diagnostics.append(
                f"PID {pid}: Weixin.exe found, but module inspection was skipped ({type(exc).__name__})."
            )
            continue
        except Exception as exc:
            diagnostics.append(
                f"PID {pid}: Weixin.exe found, but module inspection failed ({type(exc).__name__})."
            )
            continue

        if not has_weixin_dll:
            diagnostics.append(f"PID {pid}: Weixin.exe child process skipped (Weixin.dll not mapped).")
            continue

        try:
            wxinfo = dump_wechat_info_v4(pid)
        except Exception as exc:
            diagnostics.append(
                f"PID {pid}: Weixin.dll found, but account scan failed ({type(exc).__name__}: {exc})."
            )
            continue

        if wxinfo is None:
            diagnostics.append(f"PID {pid}: account scan returned no result.")
            continue

        result_v4.append(wxinfo)
        version = getattr(wxinfo, "version", "") or "unknown"
        wxid = getattr(wxinfo, "wxid", "") or "unknown"
        wx_dir = getattr(wxinfo, "wx_dir", "") or "not detected"
        key_status = "FOUND" if getattr(wxinfo, "key", "") else "NOT FOUND"
        diagnostics.append(
            f"PID {pid}: account={wxid}; version={version}; data folder={wx_dir}; database key={key_status}."
        )

    if candidate_count == 0:
        diagnostics.append("No Weixin.exe process was visible to the application.")

    return result_v4, diagnostics


def get_info_v4() -> List[WeChatInfo]:
    result_v4, _diagnostics = get_info_v4_diagnostics()
    return result_v4


def get_info_v3(version_list) -> List[WeChatInfo]:
    result = []
    for process in psutil.process_iter(["name", "exe", "pid"]):
        if process.name() == "WeChat.exe":
            pid = process.pid
            wxinfo = dump_wechat_info_v3(version_list, pid)
            result.append(wxinfo)
    return result


if __name__ == "__main__":
    import json

    file_path = r"E:\Project\Python\MemoTrace\resources\data\version_list.json"
    with open(file_path, "r", encoding="utf-8") as f:
        version_list = json.loads(f.read())

    r_4 = get_info_v4()
    r_3 = get_info_v3(version_list)
    for wx_info in r_4 + r_3:
        print(wx_info)
