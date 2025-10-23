# 上下文 - Material Identification Upper Computer
日期：2025-10-13
范围：Memory Bank 初始化与对齐。

当前工作焦点
- 建立完整 Memory Bank 文档，确保与现有实现和测试契约一致。
- 对齐协议与会话关键路径：A0/A1/AF/B0/B1/BF、SEQ/ACK、心跳调度与重试。
- 业务流水打通：watch→work/error 入库、N1/N2/N3→R/G/B 分组、蛇形映射、A1 分片与可选 attrs 承载。

最近变更
- 新增产品说明: [product.md](.kilocode/rules/memory-bank/product.md)
- 新增架构说明: [architecture.md](.kilocode/rules/memory-bank/architecture.md)
- 新增技术说明: [tech.md](.kilocode/rules/memory-bank/tech.md)
- 确认实现锚点：协议 [encode_frame()](app/comm/protocol.py:49)、解码 [decode_stream()](app/comm/protocol.py:62)、会话 [SerialSession](app/comm/session.py:28)、派发 [Dispatcher](app/business/dispatcher.py:13)、映射 [compose_indices_and_attrs_for_group()](app/business/mapping.py:107)、入库 [ingest_batch()](app/business/file_ingress.py:36)。
- 校验测试契约：心跳调度 [tests/test_heartbeat_scheduler.py](tests/test_heartbeat_scheduler.py:66)、重试策略 [tests/test_session_retry.py](tests/test_session_retry.py:65)、分片 [tests/test_chunking.py](tests/test_chunking.py:43)、闪烁 attrs [tests/test_dispatcher_blink.py](tests/test_dispatcher_blink.py:60)、日志 HEX 捕获 [tests/test_logging_config.py](tests/test_logging_config.py:51)。

下一步
- 起草并提交操作任务清单: [tasks.md](.kilocode/rules/memory-bank/tasks.md)（标准作业流：入库→分组→映射→派发→会话对接→归档）。
- 提供简要模板: [brief.md](.kilocode/rules/memory-bank/brief.md) 供产品/业务长期维护。
- 评审 Memory Bank 三份已交文档，收集修订意见并同步更新。
- 跟进打印机接入方案设计，占位实现在 [Printer](app/devices/printer.py:5)。
- 为后续“真实串口”适配预留接口替换 [SerialPortBase](app/comm/serial_port.py:4)。

待确认与风险点
- AckCode 兼容范围：当前实现对 LEN/VAL 错误码未启用，主要覆盖 UNKNOWN_TYPE/SEQ/CHECK；与下位机协议的一致性需确认。[AckCode](app/comm/protocol.py:24)
- A1 attrs 承载：仅定义 bit0 为 BLINK；后续位含义与下位机扩展需对齐。[build_a1()](app/comm/protocol.py:115)
- 蛇形映射：默认启用，依赖 mapping.cols；现场布局变更时需同步配置。[serpentine_map()](app/business/mapping.py:35)
- 心跳与离线阈值：由 comm.* 驱动，测试期短周期；生产需根据线路稳定性调整。[start_heartbeat()](app/comm/session.py:194)
- 文件半写：ready_quiet_ms 默认 0；生产落地建议>50ms，并要求“仅在空 watch 投入”纪律。[ingest_batch()](app/business/file_ingress.py:59)
- 打印版式：当前为日志占位；真实热敏打印机型号、驱动与版式模板需评审。[Printer](app/devices/printer.py:5)

运行与验证要点
- 本地运行: [main()](app/main.py:6)；切换配置使用 APP_CONFIG_PATH 注入。[ConfigRepo.load()](app/storage/config.py:13)
- 端到端模拟：用 [FakeSerialPort](app/comm/serial_port.py:26) 与 [SerialSession](app/comm/session.py:28) 连接 [Dispatcher.request_next_payload()](app/business/dispatcher.py:35)，并驱动 B1→AF→A1→BF 流程。
- 核对日志：按需开启 logging.hex.capture 以定位 TX/RX 流；见 [get_logger()](app/logs/logger.py:24)。

版本与里程碑
- 本次版本：Memory Bank 初始化（产品/架构/技术三份文档已提交）
- 下个里程碑：提交 context.md、tasks.md 与 brief.md 模板，经评审后冻结 v1 文档集