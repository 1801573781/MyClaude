"""
测试进度显示工具

提供带 spinner 动画和实时成功率的测试进度显示，
统一 /test --st-e、/test --st-a2a 等命令的进度展示形式。
"""

import sys
import time
import threading


class TestProgressDisplay:
    """带 spinner 动画和实时成功率的测试进度显示器

    用法::

        progress = TestProgressDisplay(total=10, test_type="系统测试")
        progress.start()
        # ... 执行测试 ...
        progress.update(completed=3, passed=2)
        # ... 继续执行 ...
        progress.stop()
        progress.print_final_progress()
    """

    SPINNER_CHARS = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')

    def __init__(self, total: int, test_type: str = "系统测试"):
        self.total = total
        self.test_type = test_type
        self._completed = 0
        self._passed = 0
        self._start_time = time.time()
        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        try:
            self._is_tty = sys.stdout.isatty()
        except (AttributeError, OSError):
            # sys.stdout 可能是被包装的 TeeWriter，回退检查 console 属性
            console = getattr(sys.stdout, 'console', None)
            if console is not None:
                try:
                    self._is_tty = console.isatty()
                except (AttributeError, OSError):
                    self._is_tty = False
            else:
                self._is_tty = False

    def start(self):
        """启动 spinner 线程"""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update(self, completed: int, passed: int):
        """更新进度计数（线程安全）"""
        with self._lock:
            self._completed = completed
            self._passed = passed

    def _spin(self):
        """spinner 动画主循环"""
        i = 0
        last_heartbeat = time.time()
        while not self._stop_event.is_set():
            char = self.SPINNER_CHARS[i % len(self.SPINNER_CHARS)]
            with self._lock:
                completed = self._completed
                passed = self._passed
            current = completed + 1 if completed < self.total else self.total
            elapsed = int(time.time() - self._start_time)
            if completed > 0:
                pass_rate = passed / completed * 100
                pass_str = f"通过 {passed}/{completed} ({pass_rate:.1f}%)"
            else:
                pass_str = "通过 0/0 (--%)"
            if self._is_tty:
                msg = (
                    f"  {char} 正在执行 {current}/{self.total} {self.test_type}用例 "
                    f"(已耗时 {elapsed}s) | {pass_str} ..."
                )
                sys.stdout.write(f"\r{msg.ljust(90)}")
                sys.stdout.flush()
            else:
                now = time.time()
                if now - last_heartbeat >= 5.0:
                    print(
                        f"  ... 正在执行 {current}/{self.total} {self.test_type}用例 "
                        f"(已耗时 {elapsed}s) | {pass_str}"
                    )
                    last_heartbeat = now
            time.sleep(0.15)
            i += 1

    def stop(self):
        """停止 spinner 并清除行"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._is_tty:
            sys.stdout.write(f"\r{' ' * 90}\r")
            sys.stdout.flush()

    def print_final_progress(self):
        """打印最终进度行（不带 spinner）"""
        with self._lock:
            completed = self._completed
            passed = self._passed
        if completed > 0:
            pass_rate = passed / completed * 100
            print(
                f"  进度: {completed}/{self.total} 已执行 | "
                f"通过 {passed}/{completed} ({pass_rate:.1f}%)"
            )
        else:
            print(f"  进度: {completed}/{self.total} 已执行")
