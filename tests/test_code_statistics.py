import pytest
import sys
from pathlib import Path

# 确保项目根目录在 Python Path 中，以便导入 code_statistics
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import code_statistics as cs


# ==================== count_comments 测试 ====================

class TestCountComments:
    """测试注释行统计"""

    def test_py_single_comment(self):
        lines = ["# 这是注释", "x = 1"]
        assert cs.count_comments(lines, '.py') == 1

    def test_py_docstring_double(self):
        lines = ['"""docstring"""', "x = 1", "y = 2"]
        assert cs.count_comments(lines, '.py') == 1

    def test_py_docstring_single(self):
        lines = ["'''docstring'''", "x = 1"]
        assert cs.count_comments(lines, '.py') == 1

    def test_py_no_comment(self):
        lines = ["x = 1", "y = 2", "print(x)"]
        assert cs.count_comments(lines, '.py') == 0

    def test_yaml_comment(self):
        lines = ["# 配置", "key: value"]
        assert cs.count_comments(lines, '.yaml') == 1

    def test_json_comment(self):
        lines = ["// JSON 不支持注释但识别", '{"key": "value"}']
        assert cs.count_comments(lines, '.json') == 1

    def test_unknown_ext_no_comment(self):
        lines = ["<!-- comment -->", "text"]
        assert cs.count_comments(lines, '.txt') == 0


# ==================== file_stats 测试 ====================

class TestFileStats:
    """测试单文件统计"""

    def test_pure_code(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("a = 1\nb = 2\nc = 3\n", encoding='utf-8')
        s = cs.file_stats(f)
        assert s["total"] == 3
        assert s["code"] == 3
        assert s["blank"] == 0
        assert s["comment"] == 0

    def test_with_blank_lines(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("a = 1\n\nb = 2\n\nc = 3\n", encoding='utf-8')
        s = cs.file_stats(f)
        assert s["total"] == 5
        assert s["code"] == 3
        assert s["blank"] == 2
        assert s["comment"] == 0

    def test_with_comments(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# header\na = 1\n# footer\n", encoding='utf-8')
        s = cs.file_stats(f)
        assert s["total"] == 3
        assert s["code"] == 1
        assert s["blank"] == 0
        assert s["comment"] == 2

    def test_mixed_all(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# top\n\nx = 1\n\ny = 2\n# bottom\n", encoding='utf-8')
        s = cs.file_stats(f)
        assert s["total"] == 6
        assert s["code"] == 2
        assert s["blank"] == 2
        assert s["comment"] == 2
        assert s["code"] + s["blank"] + s["comment"] == s["total"]

    def test_yaml_file(self, tmp_path):
        f = tmp_path / "config.yaml"
        f.write_text("# config\n\nkey: value\n", encoding='utf-8')
        s = cs.file_stats(f)
        assert s["total"] == 3
        assert s["comment"] == 1
        assert s["blank"] == 1
        assert s["code"] == 1


# ==================== collect_stats 测试 ====================

class TestCollectStats:
    """测试核心遍历逻辑"""

    def test_skip_dir(self, tmp_path):
        """跳过目录规则正确"""
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / ".git" / "config.txt").write_text("git config", encoding='utf-8')
        (tmp_path / "src" / "main.py").write_text("print(1)\n", encoding='utf-8')

        dir_stats, ext_stats = cs.collect_stats(tmp_path)

        # .git 目录下的文件应被跳过
        all_files = []
        for files in dir_stats.values():
            all_files.extend(f["name"] for f in files)
        assert "config.txt" not in all_files
        assert "main.py" in all_files

    def test_skip_hidden_file(self, tmp_path):
        """隐藏文件被跳过"""
        (tmp_path / ".hidden.py").write_text("secret\n", encoding='utf-8')
        (tmp_path / "normal.py").write_text("normal\n", encoding='utf-8')

        dir_stats, ext_stats = cs.collect_stats(tmp_path)

        all_files = []
        for files in dir_stats.values():
            all_files.extend(f["name"] for f in files)
        assert ".hidden.py" not in all_files
        assert "normal.py" in all_files

    def test_skip_file_in_skip_files(self, tmp_path):
        """.gitignore 在 SKIP_FILES 中，应被跳过"""
        (tmp_path / ".gitignore").write_text("*.pyc\n", encoding='utf-8')
        (tmp_path / "readme.md").write_text("hello\n", encoding='utf-8')

        dir_stats, ext_stats = cs.collect_stats(tmp_path)

        all_files = []
        for files in dir_stats.values():
            all_files.extend(f["name"] for f in files)
        assert ".gitignore" not in all_files
        assert "readme.md" in all_files

    def test_ext_filter(self, tmp_path):
        """扩展名过滤正确"""
        (tmp_path / "main.py").write_text("code\n", encoding='utf-8')
        (tmp_path / "image.png").write_text("binary\n", encoding='utf-8')
        (tmp_path / "readme.md").write_text("doc\n", encoding='utf-8')

        dir_stats, ext_stats = cs.collect_stats(tmp_path)

        all_files = []
        for files in dir_stats.values():
            all_files.extend(f["name"] for f in files)
        assert "main.py" in all_files
        assert "readme.md" in all_files
        assert "image.png" not in all_files

    def test_dir_level_summary(self, tmp_path):
        """目录级别汇总正确"""
        (tmp_path / "a.py").write_text("x = 1\n", encoding='utf-8')
        (tmp_path / "b.py").write_text("y = 2\nz = 3\n", encoding='utf-8')

        dir_stats, ext_stats = cs.collect_stats(tmp_path)

        # 根目录 "."
        assert "." in dir_stats
        root_files = {f["name"]: f for f in dir_stats["."]}
        assert len(root_files) == 2
        assert root_files["a.py"]["total"] == 1
        assert root_files["b.py"]["total"] == 2

    def test_ext_summary(self, tmp_path):
        """扩展名汇总正确"""
        (tmp_path / "a.py").write_text("x = 1\n", encoding='utf-8')
        (tmp_path / "b.py").write_text("y = 2\n\nz = 3\n", encoding='utf-8')
        (tmp_path / "readme.md").write_text("# doc\n", encoding='utf-8')

        dir_stats, ext_stats = cs.collect_stats(tmp_path)

        assert ".py" in ext_stats
        assert ext_stats[".py"]["files"] == 2
        assert ext_stats[".py"]["total"] == 4  # 1 + 3
        assert ext_stats[".md"]["files"] == 1

    def test_code_statistics_included(self, tmp_path):
        """code_statistics.py 自身被统计（不在 SKIP_FILES 中）"""
        (tmp_path / "code_statistics.py").write_text("# stats tool\nprint('hi')\n", encoding='utf-8')

        dir_stats, ext_stats = cs.collect_stats(tmp_path)

        all_files = []
        for files in dir_stats.values():
            all_files.extend(f["name"] for f in files)
        assert "code_statistics.py" in all_files

    def test_subdir_stats(self, tmp_path):
        """子目录统计正确"""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("print(1)\n", encoding='utf-8')
        (tmp_path / "config.yaml").write_text("# cfg\nkey: v\n", encoding='utf-8')

        dir_stats, ext_stats = cs.collect_stats(tmp_path)

        # 根目录有 config.yaml
        assert "." in dir_stats
        root_names = [f["name"] for f in dir_stats["."]]
        assert "config.yaml" in root_names

        # src 子目录有 main.py
        assert "src" in dir_stats
        src_names = [f["name"] for f in dir_stats["src"]]
        assert "main.py" in src_names


# ==================== 表格渲染函数 - 冒烟测试 ====================

class TestPrintFunctions:
    """验证表格渲染函数不抛异常"""

    def test_print_dir_table_no_crash(self, capsys):
        """print_dir_table 正常输出不抛异常"""
        files = [
            {"name": "a.py", "total": 10, "code": 7, "blank": 2, "comment": 1},
            {"name": "b.py", "total": 5, "code": 4, "blank": 1, "comment": 0},
        ]
        # 不应抛出异常
        cs.print_dir_table("test_dir", files)
        captured = capsys.readouterr()
        assert "a.py" in captured.out
        assert "b.py" in captured.out
        assert "目录汇总" in captured.out

    def test_print_summary_table_no_crash(self, capsys, tmp_path):
        """print_summary_table 正常输出不抛异常"""
        ext_stats = {
            ".py": {"files": 2, "total": 15, "code": 11, "blank": 3, "comment": 1},
        }
        result = cs.print_summary_table(tmp_path, ext_stats)
        assert result["files"] == 2
        assert result["total"] == 15
        captured = capsys.readouterr()
        assert ".py" in captured.out
        assert "总计" in captured.out


# ==================== main 函数冒烟测试 ====================

class TestMain:
    """验证 main 函数正常执行"""

    def test_main_no_crash(self, tmp_path):
        """main 在临时目录下运行不抛异常"""
        (tmp_path / "hello.py").write_text("# hi\nprint('hi')\n", encoding='utf-8')
        result = cs.main(str(tmp_path))
        assert "files" in result
        assert result["files"] >= 1
        assert result["total"] >= 2