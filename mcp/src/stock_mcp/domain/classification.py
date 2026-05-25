from pydantic import BaseModel, Field

class InstrumentClassification(BaseModel):
    code: str = Field(..., description="证券代码")
    primary_class: str = Field(..., description="一级资产类别")
    sector: str = Field("Unknown", description="二级行业或板块")
    source: str = Field("unknown", description="分类来源，如 config/cache/llm/unknown")
    confidence: float = Field(0.0, description="置信度")
