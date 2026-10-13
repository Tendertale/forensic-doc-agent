"""案件存储布局：原件、识别用副本与文稿分目录保存。

对应需求：

* R-19 / N-8 —— 图片处理保存原件；处理结果单独保存，不改变原始证据内容。
  原件一旦写入**永不覆盖**，重复写入同内容时返回既有路径，内容不同时另存为
  ``<stem>-<n><suffix>``，绝不动已有文件。

目录约定（`docs/architecture.md` 第 1 节）：

```text
storage/
├── templates/report_template.docx          模板原件（只读）
└── cases/<case_no>/
    ├── originals/<kind>/<filename>         上传原件，永不覆盖、永不修改
    ├── derived/<kind>/<stem>_p<N>.png      识别用副本（预处理结果）
    ├── derived/<kind>/<stem>_p<N>.ocr.json sidecar OCR 结果
    └── documents/<case_no>-<doc_type>-v<N>.docx / .pdf
```

本模块不做任何识别、渲染或数据校验，只负责路径计算与字节落盘。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional, Union

__all__ = ["DEFAULT_STORAGE_ROOT", "CaseStorage", "safe_segment", "sha256_bytes", "sha256_file"]

#: 默认存储根目录（可由环境变量 ``FORENSIC_STORAGE_ROOT`` 覆盖）。
DEFAULT_STORAGE_ROOT: str = "storage"

#: 目录名只允许中文、字母、数字、点、下划线与连字符，其余字符统一替换为下划线。
_UNSAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._\-]+")


def safe_segment(value: str, fallback: str = "unnamed") -> str:
    """把任意字符串转换为安全的单层路径片段（去掉目录分隔与危险字符）。"""

    text = str(value or "").replace("\\", "/").split("/")[-1].strip()
    text = _UNSAFE.sub("_", text).strip(" ._")
    return text or fallback


def sha256_bytes(data: bytes) -> str:
    """字节内容的 sha256 十六进制摘要。"""

    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path: Union[str, Path]) -> str:
    """文件内容的 sha256 十六进制摘要（分块读取，避免整文件入内存）。"""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CaseStorage:
    """按案件编号隔离的材料与文稿目录。

    ``root`` 之外的路径不会被写入：所有目录都由 :meth:`case_dir` 派生，
    且案件编号、材料类型、文件名都经过 :func:`safe_segment` 处理。
    """

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()

    # ------------------------------------------------------------------
    # 目录
    # ------------------------------------------------------------------
    def ensure(self) -> None:
        """建立存储根目录、模板目录与案件目录。幂等。"""

        self.templates_dir().mkdir(parents=True, exist_ok=True)
        (self.root / "cases").mkdir(parents=True, exist_ok=True)

    def templates_dir(self) -> Path:
        """模板原件目录（只读）。"""

        return self.root / "templates"

    def case_dir(self, case_no: str) -> Path:
        """某个案件的总目录。"""

        return self.root / "cases" / safe_segment(case_no, "case")

    def originals_dir(self, case_no: str, kind: str) -> Path:
        """上传原件目录：永不覆盖、永不修改。"""

        return self.case_dir(case_no) / "originals" / safe_segment(kind, "other")

    def derived_dir(self, case_no: str, kind: str) -> Path:
        """识别用副本目录：可随时重新生成。"""

        return self.case_dir(case_no) / "derived" / safe_segment(kind, "other")

    def documents_dir(self, case_no: str) -> Path:
        """生成的文稿目录。"""

        return self.case_dir(case_no) / "documents"

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def write_original(self, case_no: str, kind: str, filename: str,
                       data: Optional[bytes]) -> str:
        """写入上传原件，返回相对存储根目录的路径。

        * 目标文件不存在 → 直接写入（``data`` 为 ``None`` 时写入空文件占位）。
        * 目标文件已存在且内容一致 → 返回既有路径，不重写。
        * 目标文件已存在但内容不同 → **不覆盖**，另存为 ``<stem>-<n><suffix>``。
        * ``data`` 为 ``None`` 且目标已存在 → 视为已登记，返回既有路径。
        """

        directory = self.originals_dir(case_no, kind)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / safe_segment(filename, "original")

        if target.exists():
            if data is None:
                return self.relative(target)
            payload = bytes(data)
            if sha256_file(target) == sha256_bytes(payload):
                return self.relative(target)
            target = self._unique_path(target)

        target.write_bytes(b"" if data is None else bytes(data))
        return self.relative(target)

    def write_derived(self, case_no: str, kind: str, filename: str, data: bytes) -> str:
        """写入识别用副本（可覆盖：副本不构成证据）。返回相对路径。"""

        directory = self.derived_dir(case_no, kind)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / safe_segment(filename, "derived")
        target.write_bytes(bytes(data))
        return self.relative(target)

    # ------------------------------------------------------------------
    # 路径转换
    # ------------------------------------------------------------------
    def relative(self, path: Union[str, Path]) -> str:
        """把路径转换为相对存储根目录的 POSIX 风格字符串。

        不在根目录内时原样返回绝对路径，便于错误信息定位。
        """

        candidate = Path(path)
        if not candidate.is_absolute():
            return candidate.as_posix()
        resolved = candidate.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            return resolved.as_posix()

    def absolute(self, relative_path: Union[str, Path]) -> Path:
        """把相对路径还原为绝对路径；已是绝对路径时原样返回。"""

        candidate = Path(str(relative_path))
        if candidate.is_absolute():
            return candidate
        return (self.root / candidate).resolve()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _unique_path(target: Path) -> Path:
        counter = 1
        while True:
            candidate = target.with_name("{0}-{1}{2}".format(target.stem, counter, target.suffix))
            if not candidate.exists():
                return candidate
            counter += 1
