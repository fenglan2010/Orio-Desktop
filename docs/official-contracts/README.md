# 官方写入契约（示例，默认不会加载）

本目录只放契约格式说明和示例，**不会被运行时自动加载**，也不能作为生产契约安装。示例文件均带 `"example": true`，安装接口会拒绝它们。

公开资料里，酒店商家写入接口基本是合作方契约，不是可随便调用的公开路径：

- **携程**：未找到稳定、可公开引用的酒店商家调价写入 HTTP 文档。公开可见的价格同步接口属于玩乐/门票等（例如 ttdstp pricesync），不能当作酒店 eBooking 写接口使用。生产环境 URL 只能由合作方契约提供，本仓库不内置。
- **美团**：存在官方酒店直连开放平台（[openplatform-hotel.meituan.com](https://openplatform-hotel.meituan.com)），鉴权为合作方 MWS（partnerId + 签名）。公开页面提到商品/价格同步，但完整写入路径与请求体需在合作接入后按官方文档填写。不要在本仓库猜测或写死生产路径。
- **飞猪**：开放平台有公开文档。网关 `https://eco.taobao.com/router/rest`，方法如 `taobao.xhotel.rates.increment` / `taobao.xhotel.rate.update`，需 AppKey、session 与 TOP 签名。该 URL 来自官方文档，可写在示例契约中，但**默认演示不会加载、也不会自动请求该地址**。

运行时只从以下位置加载契约（需同时满足渠道已授权、服务器凭证、写入开关），且不会回退到本目录：

1. 环境变量 `OIRO_OFFICIAL_CONTRACT_CTRIP` / `_MEITUAN` / `_FLIGGY` 指向的 JSON 文件
2. 目录 `OIRO_OFFICIAL_CONTRACT_DIR/{platform}.json`
3. 服务器配置项 `hotel:{id}:official_contract_{platform}`（由运维端校验后安装或卸载；酒店账号不能安装契约）

由环境变量或目录管理的契约不能从运维端删除，也不会删除服务器上的文件。酒店侧只展示契约是否已启用和已开放动作，不展示主机名或来源。运维端可以看到来源、主机名和支持的动作，仍不回显密钥或契约全文。

契约必须包含：`platform`、`auth`、至少一个含 `method` / `path` 的动作，以及 `base_url`（可写在根上或动作上）。非本机地址必须是 https；本机测试可用 `http://127.0.0.1` 或 `localhost`。生产环境 URL 必须来自合作方正式契约，本仓库不编造携程/美团/飞猪写入路径。

## PMS/CRS 预留契约

`pms/` 目录只放官方 PMS/CRS 契约格式示例，同样**不会被运行时自动加载**，也不能作为生产契约安装（均带 `"example": true`）。本仓库不编造 Opera、Cloudbeds、Mews 或其他生产环境主机；`base_url` 与 `path` 必须由合作方正式契约提供。

运行时加载顺序（PMS/CRS 专用，不会回退到本目录或 OTA 契约目录）：

1. 环境变量 `OIRO_OFFICIAL_PMS_CONTRACT` / `OIRO_OFFICIAL_CRS_CONTRACT` 指向的 JSON 文件
2. 目录 `OIRO_OFFICIAL_PMS_CONTRACT_DIR/{kind}.json`（也可按租户或酒店 ID 分子目录）
3. 服务器配置项 `hotel:{id}:official_pms_contract_{kind}`（由运维端校验后安装或卸载；酒店账号不能安装契约）

预留动作包括库存/房态、房型和在手订单（OTB）拉取。采集任务仅在正式（非示例）契约已启用、凭证可用且运维端打开读取开关时出站；否则安静跳过，不覆盖演示数据。这不是可单独售卖的轻量 PMS。

## 电子合同预留契约

`esign/` 目录只放官方电子合同契约格式示例，同样**不会被运行时自动加载**，也不能作为生产契约安装（均带 `"example": true`，`base_url` 为空）。本仓库不编造电子签或支付生产环境主机；`base_url` 与 `path` 必须由合作方正式契约提供。电子签与支付供应商尚未接通。

运行时加载顺序（电子合同专用，不会回退到本目录或 OTA / PMS 契约目录）：

1. 环境变量 `OIRO_OFFICIAL_ESIGN_CONTRACT` 指向的 JSON 文件
2. 目录 `OIRO_OFFICIAL_ESIGN_CONTRACT_DIR/esign.json`（也可按租户或酒店 ID 分子目录）
3. 服务器配置项 `hotel:{id}:official_esign_contract`（由运维端合同岗校验后安装或卸载；酒店账号不能安装契约）

出站仅在正式（非示例）契约已启用、凭证可用且出站开关打开时发生；否则拒绝并保持等待供应商。这不是计费产品，也不包含完整的电子签署界面。

