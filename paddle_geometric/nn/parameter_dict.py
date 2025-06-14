from typing import Final, Iterable, Mapping, Optional, Tuple, Union, Any

import paddle

Key = Union[str, Tuple[str, ...]]


# `paddle.nn.LayerDict` doesn't allow `.` to be used in key names.
# This `ParameterDict` will support it by converting the `.` to `#` in the
# internal representation and converts it back to `.` in the external
# representation. It also allows passing tuples as keys.
class ParameterDict(paddle.nn.LayerDict):
    CLASS_ATTRS: Final[Tuple[str, ...]] = set(dir(paddle.nn.LayerDict))

    def __init__(
        self,
        parameters: Optional[Mapping[Key, Any]] = None,
    ):
        # Replace the keys in parameters.
        if parameters:
            parameters = {
                self.to_internal_key(key): parameter
                for key, parameter in parameters.items()
            }
        super().__init__(parameters)

    @classmethod
    def to_internal_key(cls, key: Key) -> str:
        if isinstance(key, tuple):  # ParameterDict can't handle tuples as keys
            assert len(key) > 1
            key = f"<{'___'.join(key)}>"
        assert isinstance(key, str)

        # ParameterDict cannot handle keys that exist as class attributes:
        if key in cls.CLASS_ATTRS:
            key = f'<{key}>'

        # ParameterDict cannot handle dots in keys:
        return key.replace('.', '#')

    @classmethod
    def to_external_key(cls, key: str) -> Key:
        key = key.replace('#', '.')

        if key[0] == '<' and key[-1] == '>' and key[1:-1] in cls.CLASS_ATTRS:
            key = key[1:-1]

        if key[0] == '<' and key[-1] == '>' and '___' in key:
            key = tuple(key[1:-1].split('___'))

        return key

    def __getitem__(self, key: Key):
        return super().__getitem__(self.to_internal_key(key))

    def __setitem__(self, key: Key, parameter: Any):
        return super().__setitem__(self.to_internal_key(key), parameter)

    def __delitem__(self, key: Key):
        return super().__delitem__(self.to_internal_key(key))

    def __contains__(self, key: Key) -> bool:
        return super().__contains__(self.to_internal_key(key))

    def keys(self) -> Iterable[Key]:
        return [self.to_external_key(key) for key in super().keys()]

    def items(self) -> Iterable[Tuple[Key, Any]]:
        return [(self.to_external_key(k), v) for k, v in super().items()]
