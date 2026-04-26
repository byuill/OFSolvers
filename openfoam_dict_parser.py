import os
import re
from typing import Dict


class OpenFoamDictParser:
    """
    Lightweight OpenFOAM dictionary writer focused on boundaryField replacement.

    It preserves existing file content when present and only replaces the
    boundaryField block. If no boundaryField exists, one is appended.
    """

    def __init__(
        self,
        target_path: str,
        default_class: str,
        object_name: str,
        default_dimensions: str | None = None,
        default_internal_field: str | None = None,
    ):
        self.target_path = target_path
        self.default_class = default_class
        self.object_name = object_name
        self.default_dimensions = default_dimensions or "[0 0 0 0 0 0 0]"
        self.default_internal_field = default_internal_field or "uniform 0"

    def write(self, boundary_field: Dict[str, Dict[str, str]]):
        os.makedirs(os.path.dirname(self.target_path), exist_ok=True)
        content = self._read_or_create_template()
        new_boundary_block = self._format_boundary_field(boundary_field)
        updated = self._replace_or_append_boundary_field(content, new_boundary_block)
        with open(self.target_path, "w", encoding="utf-8") as f:
            f.write(updated)

    def _read_or_create_template(self) -> str:
        if os.path.exists(self.target_path):
            with open(self.target_path, "r", encoding="utf-8") as f:
                return f.read()

        return (
            "FoamFile\n"
            "{\n"
            "    version     2.0;\n"
            "    format      ascii;\n"
            f"    class       {self.default_class};\n"
            f"    object      {self.object_name};\n"
            "}\n"
            "\n"
            f"dimensions      {self.default_dimensions};\n"
            f"internalField   {self.default_internal_field};\n"
            "\n"
            "boundaryField\n"
            "{\n"
            "}\n"
        )

    def _format_boundary_field(self, boundary_field: Dict[str, Dict[str, str]]) -> str:
        lines = ["boundaryField", "{"]
        for patch_name, attrs in boundary_field.items():
            lines.append(f"    {patch_name}")
            lines.append("    {")
            for key, value in attrs.items():
                lines.append(f"        {key:<16}{value};")
            lines.append("    }")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def _replace_or_append_boundary_field(self, content: str, new_boundary_block: str) -> str:
        match = re.search(r"\bboundaryField\b\s*\{", content)
        if not match:
            if content.endswith("\n"):
                return content + "\n" + new_boundary_block
            return content + "\n\n" + new_boundary_block

        opening_brace_idx = content.find("{", match.start())
        closing_brace_idx = self._find_matching_brace(content, opening_brace_idx)
        if closing_brace_idx == -1:
            # Fallback: append if malformed.
            return content + "\n\n" + new_boundary_block

        return content[:match.start()] + new_boundary_block + content[closing_brace_idx + 1 :]

    @staticmethod
    def _find_matching_brace(text: str, open_idx: int) -> int:
        depth = 0
        for i in range(open_idx, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i
        return -1
