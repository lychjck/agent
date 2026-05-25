from typing import Any, Dict, List
from pydantic import BaseModel, Field

class Snapshot(BaseModel):
    generated_at: str = Field(..., description="生成时间")
    holdings: List[Dict[str, Any]] = Field(default_factory=list, description="持仓事实")
    portfolio_profile: Dict[str, Any] = Field(default_factory=dict, description="画像分析")
