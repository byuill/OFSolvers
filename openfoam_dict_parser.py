import os
from typing import Dict
from foam_io import atomic_write, entries, matching, tokens


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
        atomic_write(self.target_path, self.render(boundary_field))

    def render(self, boundary_field, merge=False):
        content = self._read_or_create_template()
        if merge:
            block = entries(content).get('boundaryField')
            if block is None or not block.block:
                raise ValueError('An existing boundaryField dictionary is required.')
            body = content[block.value_start+1:block.value_end-1]
            patches = entries(body)
            replacements = []
            for name, attrs in boundary_field.items():
                item = patches.get(name)
                if item is None or not item.block:
                    raise ValueError(f'Patch {name} is not an explicit field boundary entry.')
                replacement = self._format_boundary_field({name: attrs}).split('{', 1)[1].rsplit('}', 1)[0]
                replacements.append((item.start, item.end, replacement.strip()))
            for start, end, replacement in sorted(replacements, reverse=True):
                body = body[:start]+replacement+body[end:]
            return content[:block.value_start+1]+body+content[block.value_end-1:]
        return self._replace_or_append_boundary_field(content, self._format_boundary_field(boundary_field))

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
                width = max(16, len(key)+1)
                lines.append(f'        {key:<{width}}{value};')
            lines.append("    }")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def _replace_or_append_boundary_field(
        self, content: str, new_boundary_block: str
    ) -> str:
        block = entries(content).get('boundaryField')
        if block is None:
            if content.endswith("\n"):
                return content + "\n" + new_boundary_block
            return content + "\n\n" + new_boundary_block

        if not block.block:
            raise ValueError('boundaryField must be a dictionary.')

        return (
            content[: block.start]
            + new_boundary_block
            + content[block.end :]
        )

    @staticmethod
    def _find_matching_brace(text: str, open_idx: int) -> int:
        items = tokens(text)
        for i, item in enumerate(items):
            if item.start() == open_idx:
                return items[matching(items, i)].start()
        raise ValueError('Opening brace not found.')
