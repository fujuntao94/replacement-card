import requests
import logging
import json
import uuid

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("feishu_remedy_approval")
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """获取飞书 tenant_access_token"""
    logger.info("开始获取飞书Token...")
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    data = {"app_id": app_id, "app_secret": app_secret}

    try:
        response = requests.post(url, headers=headers, json=data, timeout=15, verify=False)
        result = response.json()
        if result.get("code") != 0:
            logger.error(f"获取Token失败: {result['msg']}")
            return ""
        token = result.get("tenant_access_token", "")
        logger.info(f"Token获取成功: {token[:20]}...")
        return token
    except Exception as e:
        logger.error(f"获取Token异常: {str(e)}", exc_info=True)
        return ""


def create_remedy_approval(
    app_id: str,
    app_secret: str,
    approval_code: str,
    user_id: str,
    abnormal_date: str = "2026-03-12",
    abnormal_record: str = "应下班 18:00，缺卡",
    remedy_time: str = "2026-03-12 18:00",
    remedy_reason: str = "忘记打卡"
) -> dict:
    """创建飞书补卡审批实例（匹配自定义模板）"""
    logger.info("========== 开始发起补卡审批 ==========")

    # 1. 获取Token
    token = get_tenant_access_token(app_id, app_secret)
    if not token:
        return {"code": -1, "msg": "Token获取失败", "instance_code": ""}

    # 2. 构造表单数据（1:1匹配你的自定义模板ID）
    remedy_form = [
        {"id": "ReplacementCardDate", "type": "input", "value": abnormal_date},    # 异常日期
        {"id": "ReplacementCardRecord", "type": "input", "value": abnormal_record},  # 异常记录
        {"id": "ReplacementCardTime", "type": "input", "value": remedy_time},      # 补卡时间
        {"id": "ReplacementCardReason", "type": "textarea", "value": remedy_reason}  # 补卡事理
    ]

    # 3. 发起审批请求
    url = "https://open.feishu.cn/open-apis/approval/v4/instances"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    print(remedy_form)
    request_data = {
        "approval_code": approval_code,
        "user_id": user_id,
        "form": json.dumps(remedy_form, ensure_ascii=False),
        "uuid": str(uuid.uuid4()),
        "with_link": True
    }

    try:
        response = requests.post(url, headers=headers, json=request_data, timeout=20, verify=False)
        result = response.json()

        if result.get("code") != 0:
            logger.error(f"发起补卡审批失败: {result['msg']}")
            return {"code": result["code"], "msg": f"发起失败: {result['msg']}", "instance_code": ""}

        logger.info("补卡审批发起成功！")
        return {
            "code": 0,
            "msg": "发起成功",
            "instance_code": result.get("data", {}).get("instance_code", ""),
            "approval_url": result.get("data", {}).get("approval_url", "")
        }

    except Exception as e:
        logger.error(f"发起审批异常: {str(e)}", exc_info=True)
        return {"code": -3, "msg": f"接口异常: {str(e)}", "instance_code": ""}


def main():
    # ========== 你的配置 ==========
    APP_ID = "cli_a93bb2cf4d789cc9"
    APP_SECRET = "aFyVc072SUFfSir3WgwBCd678ShnbwWO"
    APPROVAL_CODE = "5E6B37FC-CB66-4B84-8F27-93B3C47D7F15"
    USER_ID = "b5491ce9"

    # 补卡信息配置（从外部数据解析）
    EXTERNAL_REMEDY_DATA = {
        'user_id': 'b5491ce9',
        'remedy_date': 20260312,
        'punch_no': 0,
        'work_type': 1,
        'remedy_time': '2026-03-12 09:00',
        'reason': '忘记打卡',
        'time': '-'
    }
    
    # 优化：班次类型映射（让异常记录更易读）
    work_type_map = {1: "上班", 2: "下班", -1: "未知类型"}
    work_type_desc = work_type_map.get(EXTERNAL_REMEDY_DATA['work_type'], "未知类型")
    
    # 解析外部数据为模板所需格式
    abnormal_date = str(EXTERNAL_REMEDY_DATA['remedy_date'])  # 转为字符串
    abnormal_date = f"{abnormal_date[:4]}-{abnormal_date[4:6]}-{abnormal_date[6:]}"  # 20260312 → 2026-03-12
    abnormal_record = f"缺卡类型：未打卡，班次类型：{work_type_desc}"
    remedy_time = EXTERNAL_REMEDY_DATA['remedy_time']
    remedy_reason = EXTERNAL_REMEDY_DATA['reason']

    # 发起审批
    res = create_remedy_approval(
        app_id=APP_ID,
        app_secret=APP_SECRET,
        approval_code=APPROVAL_CODE,
        user_id=USER_ID,
        abnormal_date=abnormal_date,
        abnormal_record=abnormal_record,
        remedy_time=remedy_time,
        remedy_reason=remedy_reason
    )

    print("\n========== 最终结果 ==========")
    if res["code"] == 0:
        print(f"✅ 发起成功！单号：{res['instance_code']}")
        if res.get("approval_url"):
            print(f"✅ 审批链接：{res['approval_url']}")
    else:
        print(f"❌ 失败原因：{res['msg']}")
    print("==============================\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 程序执行异常：{str(e)}")
        logger.error("程序执行异常", exc_info=True)