"""节点级错误兜底：把异常转成 errors 字段写回 state，绝不让异常逃出节点"""

from __future__ import annotations

import functools
import traceback
from typing import Any, Callable


def safe_node(name: str) -> Callable:
    """装饰器：节点抛出的任何异常都转换成 state['errors'] 追加项

    被装饰的节点不允许把 errors 字段写成 list 之外的类型；
    成功路径直接返回原函数结果，不修改任何东西。
    """

    def decorator(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        @functools.wraps(func)
        def wrapped(state, *args, **kwargs):
            try:
                return func(state, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                tb = traceback.format_exc(limit=3)
                return {
                    "errors": [{
                        "node": name,
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                        "traceback": tb,
                    }],
                }
        return wrapped

    return decorator
