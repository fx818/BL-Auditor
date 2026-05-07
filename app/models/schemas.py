from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel


class McatPoolItem(BaseModel):
    mcat_id: str
    mcat_img_url: str
    mcat_name: str
    mcat_source: str


class AuditPayload(BaseModel):
    ISQ: List[Any] = []
    approval_status: int = 10
    custtype: str = "53"
    display_name: str = ""
    fk_mcat_type_id: str = ""
    glcat_mcat_image_display: int = 3
    glid: int = 0
    image_id: str = "-1"
    img_url: str = ""
    item_desc: str = ""
    item_name: str = ""
    mcat_flag: str = "-1"
    mcat_id: str = ""
    mcat_name: str = ""
    mcat_pool: List[McatPoolItem] = []
    modid: str = "GLADMIN"
    pc_item_id: int = 0
    price: float = 0
    rejection_code: int = 0
    screen_name: str = "live_product_approval"
    secondary_mcats: List[Any] = []
    unit: str = "Piece"
    worker_name: str = "Product_Approval_Auditor_1.2"


class AuditResponse(BaseModel):
    data: Dict[str, Any]
    payload: Dict[str, Any]
