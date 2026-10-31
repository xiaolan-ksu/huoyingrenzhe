"""替身计时器 —— 程序入口。

用法：
    pip install -r requirements.txt
    python main.py

全部操作都在悬浮面板上：
    点中间数字   手动打一次倒计时
    P1 ⇄         换边（换完自动监测对面）
    ☰            设置：冷却秒数、多帧确认、最小间隔
    ◎            显示/隐藏检测点，可拖动圆圈标定
    ⏸            暂停 / 恢复像素检测
    ↺            清空当前倒计时

退出：右键托盘图标选「退出」。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QSharedMemory, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QDialog

from core import config as config_module
from core.chooser import PlayerChooser
from core.debug_overlay import DebugOverlay
from core.detect import SideTracker
from core.foreground import find_window_client_rect, get_foreground_title, title_matches
from core.overlay import Overlay
from core.sampler import ScreenSampler
from core.settings import SettingsDialog


_DEBUG = os.environ.get("TIMER_DEBUG") == "1"

# 单实例锁要在整个进程生命周期内保持引用，否则函数一返回锁就没了
_INSTANCE_GUARD: QSharedMemory | None = None


def make_console_safe() -> None:
    """让控制台输出永远不因编码问题把进程带走。

    打包成 exe 后 stdout 是 GBK。一旦要打印的字符串里含 GBK 编不了的字符
    ——窗口标题里的零宽空格、emoji、生僻符号——print 就会抛
    UnicodeEncodeError。这个异常如果落在 Qt 槽函数或启动流程里，进程
    会被直接终止（连 traceback 都来不及看）。

    设成 errors="replace" 后，编不了的字符输出成 ?，程序继续跑。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            # stdout 被重定向成管道、或者 PyInstaller 的窗口模式下没有
            # 真实流，reconfigure 可能不可用。此时不处理也不该崩。
            pass


def apply_dpi_mode() -> None:
    """关闭进程的 DPI 感知，让屏幕坐标与配置里的像素值保持同一套刻度。

    必须在 QApplication 创建之前调用。开着系统缩放（125%/150%）时，
    grabWindow 拿到的图和配置里的坐标会对不上——同一个点，一边按逻辑
    像素算、一边按物理像素算，差出一个缩放倍数。设成 DPI 不感知后
    两边都是物理像素，标定出来的坐标才能稳定复用。
    """
    if sys.platform != "win32":
        return

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_DPI_UNAWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class Runtime:
    """把配置、取样器、悬浮窗和热键粘在一起。"""

    def __init__(self, cfg: dict, player: str = "p1") -> None:
        self.cfg = cfg
        self.overlay = Overlay(cfg)
        self.sampler = ScreenSampler(cfg)

        # 选自己是哪一方，但监测的是**对面**那一侧：
        # 实战里自己剩几个替身心知肚明，真正需要盯的是对手还剩几个。
        self._player = player
        self._own_side = "left" if player == "p1" else "right"
        self._target = "right" if player == "p1" else "left"
        det = cfg["detection"]
        self._tracker = SideTracker(
            confirm_frames=int(det.get("confirm_frames", 2)),
            min_gap=float(det.get("min_trigger_gap", 3.0)),
            prefix_check=bool(det.get("prefix_check", True)),
        )
        self.overlay.set_player(player)
        self.overlay.switch_side.connect(self._toggle_side)
        self.overlay.open_settings.connect(self._open_settings)
        self._settings_open = False

        self.overlay.quit_requested.connect(self.quit)
        self.overlay.toggle_debug.connect(self._toggle_debug)
        self.overlay.toggle_pause.connect(self._toggle_pause)
        self.overlay.clear_requested.connect(self._clear_countdown)
        self.overlay.manual_trigger.connect(self.overlay.start_countdown)

        self._poll = QTimer()
        self._poll.timeout.connect(self.poll_once)

        self._last_error: str | None = None
        self._last_skipped = 0
        self._detecting = bool(cfg["detection"]["enabled"])
        self._fg_keywords = list(cfg["detection"].get("require_foreground") or [])
        self._map_keyword = str(cfg["detection"].get("map_to_window") or "")
        self._fg_matched: bool | None = None
        self._fg_checked_at = 0.0

        self._debug_view = DebugOverlay(cfg)
        self._debug_view.points_moved.connect(self._save_debug_points)
        self._debug_on = False
        self._debug_ticks = 0
        self._last_remaining: tuple[int | None, int | None] | None = None

    def start(self) -> None:
        self.overlay.show()

        geo = self.overlay.geometry()
        print(f"[窗口] 悬浮窗位置 ({geo.x()}, {geo.y()})，大小 {geo.width()}×{geo.height()}")
        print(f"[窗口] 是否可见：{self.overlay.isVisible()}（面板上的按钮就是全部操作入口）")
        self.overlay.show_status("点数字手动打点，右上按钮可标定", 8000)

        # 定时器总是跑，实际是否采样由 self._detecting 决定，
        # 这样暂停键在任何状态下都能把检测重新打开。
        interval = int(self.cfg["poll_interval_ms"])
        self._poll.start(interval)

        if self._detecting:
            print(f"[检测] 已启动，每 {interval}ms 采样一次")
        else:
            print("[检测] 初始关闭（仅手动打点），点面板上的暂停按钮可随时开启")

        if self._fg_keywords:
            current = get_foreground_title()
            print(f"[检测] 前台标题需包含：{' / '.join(self._fg_keywords)}")
            print(f"[检测] 当前前台窗口：{current[:60] or '(取不到标题)'}")

        # 不管有没有配窗口都解析一次并打印，否则换分辨率后用户看不到
        # 映射到底算成了多少，只能靠猜
        rect = find_window_client_rect(self._map_keyword) if self._map_keyword else None
        self.sampler.set_window(rect)
        print(f"[映射] {self.sampler.describe_mapping()}")
        region = self.sampler.left_region
        if region and region.points:
            print(f"[映射] 换算后左一豆检测点 → {region.points[0]}")
        if self._map_keyword and rect is None:
            print(f"[映射] 没找到窗口 {self._map_keyword!r}，按整屏算")

        print("[就绪] 点中间数字=手动打点；按钮依次是 换边/设置/标定/暂停/清空")

    def _foreground_ok(self) -> bool:
        """判断游戏/模拟器是否在前台，不在前台就别费劲看屏幕了。

        不做这个判断的话，桌面上任意内容都可能被当成"替身消耗"，
        倒计时会莫名其妙刷屏。标题关键词在 config.json 里配。
        """
        if not self._fg_keywords:
            return True

        now = time.monotonic()
        if self._fg_matched is not None and now - self._fg_checked_at < 0.5:
            return self._fg_matched

        title = get_foreground_title()
        matched = title_matches(title, self._fg_keywords)
        self._fg_checked_at = now

        # 顺手刷新画面区域：模拟器一挪位置、改大小、换分辨率，坐标要跟着换算
        rect = find_window_client_rect(self._map_keyword) if self._map_keyword else None
        if self.sampler.set_window(rect):
            # 映射一变，检测点整体移动，旧基线不可信，必须重置，
            # 否则窗口最大化/还原时会凭空多出一次"替身消耗"
            self._tracker.reset()
            print(f"[映射] {self.sampler.describe_mapping()}", flush=True)

        if matched != self._fg_matched:
            self._fg_matched = matched
            if matched:
                print(f"[检测] 前台切到目标窗口：{title[:60]}，开始检测", flush=True)
            else:
                print(f"[检测] 前台不是目标窗口（{title[:40] or '无标题'}），暂停检测", flush=True)
                # 切走时清掉基线，免得切回来把"数量回升/跳变"误判成消耗
                self._tracker.reset()

        return matched

    def poll_once(self) -> None:
        """一次采样：只看对面那一侧的豆，少了就说明对手用了替身。"""
        if not self._detecting:
            return

        # 调试模式强制采样，即使没在游戏前台，方便把圈对准豆
        if not self._debug_on and not self._foreground_ok():
            return

        try:
            region = self.sampler.left_region if self._target == "left" else self.sampler.right_region
            states = self.sampler.sample(region)
            used = self._tracker.feed(states)

            if self._debug_on:
                self._refresh_debug(states)
            else:
                if used:
                    self.overlay.start_countdown()


            # 剩余数一变就打印，方便对照"对手用替身时程序到底看到了什么"
            if self._tracker.remaining != self._last_remaining:
                self._last_remaining = self._tracker.remaining
                print(
                    f"[状态] 对面({self._target}) 可用={self._tracker.remaining}  点={states}",
                    flush=True,
                )

            # 排列校验丢帧数。数字一直往上涨说明豆的排列跟"从一端连续"
            # 这个假设不符，把 config.json 里 prefix_check 设成 false 就行。
            if self._tracker.skipped != self._last_skipped:
                self._last_skipped = self._tracker.skipped
                if self._tracker.skipped == 1 or self._tracker.skipped % 20 == 0:
                    print(
                        f"[检测] 排列矛盾已丢弃 {self._tracker.skipped} 帧"
                        f"（持续增长就把 prefix_check 关掉）",
                        flush=True,
                    )
        except Exception as exc:
            # 槽函数里漏出去的异常会让 PyQt 直接 abort 整个进程（0xC0000409），
            # 所以这里必须自己兜住，一个采样异常不该把程序带走。
            print(f"[检测] 采样异常：{exc!r}", flush=True)

        # 截屏失败时不要每 100ms 刷一行，只在错误变化时提示一次
        error = self.sampler.last_error
        if error and error != self._last_error:
            print(f"[检测] {error}", flush=True)
        self._last_error = error

    def _refresh_debug(self, states: list) -> None:
        region = self.sampler.left_region if self._target == "left" else self.sampler.right_region
        if region is None:
            return

        prefix = "L" if self._target == "left" else "R"
        points: list[tuple[int, int, str, str | None]] = [
            (pt[0], pt[1], f"{prefix}{index}", state)
            for index, (state, pt) in enumerate(zip(states, region.points), start=1)
        ]
        self._debug_view.set_points(points)

        # F3 开启时每 3 秒把原始读数打到控制台，方便对照
        self._debug_ticks += 1
        if self._debug_ticks % 30 == 0:
            print(f"[调试] 对面({self._target}) 点={states}", flush=True)

    def _save_debug_points(self) -> None:
        """拖动结束：把屏幕坐标换算成参考坐标写回 config.json。"""
        refs = [self.sampler.unmap((x, y)) for x, y, _l, _s in self._debug_view.points()]
        if not refs:
            return

        current = config_module.load(config_path())
        left = list(current["detection"]["left_points"])
        right = list(current["detection"]["right_points"])
        if self._target == "left":
            left = refs
        else:
            right = refs

        config_module.save_detection_points(config_path(), left, right)
        self.sampler.set_points(left, right)
        print(f"[标定] 已保存对面({self._target}) 的 {len(refs)} 个点 → config.json", flush=True)
        self.overlay.show_status("标定已保存到 config.json", 2000)

    def _toggle_debug(self) -> None:
        self._debug_on = not self._debug_on
        if self._debug_on:
            self._debug_view.show()
            self._debug_view.raise_()
            print(
                "[调试] 检测点已显示：绿=可用 黄=赤金 灰=已用 红=没识别到",
                flush=True,
            )
            print("[调试] 左键拖动圆圈对准豆，松手自动保存；再按 F3 关闭", flush=True)
        else:
            self._debug_view.hide()
            print("[调试] 已关闭检测点标记", flush=True)

    def _open_settings(self) -> None:
        """打开设置窗口。期间屏蔽全局热键，否则按下 z 会顺手打出计时。"""
        self._settings_open = True
        try:
            dialog = SettingsDialog(self.cfg, self.overlay)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

            values = dialog.values()
            self.cfg["countdown_seconds"] = values["countdown_seconds"]
            self.cfg["detection"]["confirm_frames"] = values["confirm_frames"]
            self.cfg["detection"]["min_trigger_gap"] = values["min_trigger_gap"]

            # 判定参数变了，重建跟踪器让新参数立刻生效
            self._tracker = SideTracker(
                confirm_frames=values["confirm_frames"],
                min_gap=values["min_trigger_gap"],
                prefix_check=bool(self.cfg["detection"].get("prefix_check", True)),
            )
            self._last_remaining = None
            self.overlay.set_total(values["countdown_seconds"])
            self.overlay.show_status("设置已保存", 1800)

            config_module.save_runtime(config_path(), self.cfg)

            print(
                f"[设置] 已保存：冷却 {values['countdown_seconds']}s，"
                f"确认 {values['confirm_frames']} 帧，间隔 {values['min_trigger_gap']}s",
                flush=True,
            )
        finally:
            self._settings_open = False

    def _toggle_pause(self) -> None:
        """暂停 / 恢复像素检测。"""
        self._detecting = not self._detecting
        self.overlay.set_paused(not self._detecting)
        state = "恢复" if self._detecting else "暂停"
        self.overlay.show_status(f"检测已{state}", 1500)
        print(f"[检测] 已{state}", flush=True)

    def _clear_countdown(self) -> None:
        """清空当前计时并重置基线。"""
        self.overlay.clear()
        self._tracker.reset()
        self._last_remaining = None
        self.overlay.show_status("已清空", 1200)

    def _toggle_side(self) -> None:
        """换边：P1 ⇄ P2，同时把监测目标切到对面。"""
        self._player = "p2" if self._player == "p1" else "p1"
        self._own_side = "left" if self._player == "p1" else "right"
        self._target = "right" if self._player == "p1" else "left"

        self._tracker.reset()
        self._last_remaining = None
        self.overlay.set_player(self._player)
        self.overlay.clear()

        config_module.save_player(config_path(), self._player)

        mine = "左侧" if self._own_side == "left" else "右侧"
        theirs = "右侧" if self._target == "right" else "左侧"
        print(f"[切换] 你在{mine}，监测对面（{theirs}）", flush=True)
        self.overlay.show_status(f"监测对面（{theirs}）", 1600)

    def probe(self) -> None:
        """打印鼠标位置的三种信息，供手工标定使用。

        屏幕坐标是"现在鼠标在哪"，参考坐标是"相对 1920×1080 画布在哪"，
        后者才能直接写进 config.json 的 left_points / right_points。
        """
        pos = QCursor.pos()
        screen_x, screen_y = pos.x(), pos.y()
        rgb = self.sampler.probe(screen_x, screen_y)
        ref_x, ref_y = self.sampler.unmap((screen_x, screen_y))

        if rgb is None:
            self.overlay.show_status(f"({screen_x}, {screen_y})  取色失败")
        else:
            self.overlay.show_status(f"({screen_x}, {screen_y})   RGB {rgb}")

        print(
            f"[标定] 屏幕 ({screen_x}, {screen_y})  →  参考 [{ref_x}, {ref_y}]   RGB {rgb}",
            flush=True,
        )
        print(f"[标定] 当前前台窗口：{get_foreground_title()[:60] or '(取不到标题)'}", flush=True)

    def quit(self) -> None:
        self._poll.stop()
        self.overlay.clear()
        self.overlay.hide()
        QApplication.instance().quit()


def config_path() -> Path:
    """配置文件路径。

    打包成 exe 后 __file__ 指向的是临时解压目录（_MEIPASS），
    配置必须跟着 exe 本体走，否则用户在 exe 旁边改的 config.json 不生效。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name("config.json")
    return Path(__file__).with_name("config.json")


def main() -> int:
    make_console_safe()

    cfg_path = config_path()
    cfg = config_module.load(cfg_path)

    if cfg.get("dpi_unaware", True):
        apply_dpi_mode()

    app = QApplication(sys.argv)
    # 悬浮窗被隐藏到托盘时不要让进程跟着退出
    app.setQuitOnLastWindowClosed(False)

    # 单实例保护：开两个会抢同一套全局热键、两个悬浮窗还互相遮挡，
    # 表现就是"用掉替身没反应"或者计时器闪一下就没
    global _INSTANCE_GUARD
    _INSTANCE_GUARD = QSharedMemory("substitution_timer_single_instance")
    if not _INSTANCE_GUARD.create(1):
        print("[提示] 程序已经在运行了，不要重复打开。")
        print("       找不到面板就看托盘图标，单击可以显示 / 隐藏。")
        return 1

    for message in config_module.validate(cfg):
        print(f"[配置] {message}")

    # 开局选自己是哪一方，程序监测的是对面那一侧
    player = str(cfg.get("player") or "p1")
    if cfg.get("ask_player_on_start", True) and os.environ.get("TIMER_NO_DIALOG") != "1":
        dialog = PlayerChooser(current=player)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.choice:
            print("[提示] 没有选择位置，已退出。")
            return 0
        player = dialog.choice
        config_module.save_player(cfg_path, player)

    mine = "左侧" if player == "p1" else "右侧"
    theirs = "右侧" if player == "p1" else "左侧"
    print(f"[位置] 你在{mine}，监测对面（{theirs}）的替身豆")

    runtime = Runtime(cfg, player)
    runtime.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
