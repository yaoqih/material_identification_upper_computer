# 任务清单 - Material Identification Upper Computer

说明
- 本清单记录常用的标准作业流与可重复任务，聚焦“该做什么、改哪些文件、注意什么”。
- 所列步骤均已与实现与测试契约对齐，引用可点击锚点以便溯源。

通用先决条件
- Python 3.10+ 环境，依赖见 [requirements.txt](requirements.txt)
- 可选通过环境变量注入配置：APP_CONFIG_PATH 指向自定义 JSON（覆盖 [configs/default.json](configs/default.json) 的默认项），加载逻辑见 [ConfigRepo.load()](app/storage/config.py:13)

1) 初始化与运行脚手架
- 目的：启动应用骨架，验证日志与配置生效。
- 步骤：
  1. 准备配置文件（可直接使用 [configs/default.json](configs/default.json)）。
  2. 运行入口 [main()](app/main.py:6) 验证启动日志。
  3. 如需切换配置，先设置 APP_CONFIG_PATH，再运行。
- 参考：日志获取 [get_logger()](app/logs/logger.py:24)

2) 文件入库（watch→work/error，安静窗口）
- 目的：将共享目录中的 .txt 与 .jpg/.jpeg 成对文件以原子方式剪切入 work。
- 修改/使用的代码：
  - 入库服务 [FileIngressService.ingest_batch()](app/business/file_ingress.py:36)
  - 原子移动 [FileIngressService._safe_move()](app/business/file_ingress.py:115)
- 步骤：
  1. 将同名的 .txt 与 .jpg 放入 watch 目录（默认 data/watch）。
  2. 配置 ingress.ready_quiet_ms（毫秒），避免“半写”文件被处理。
  3. 调用 ingest_batch()，完整对移动至 work，不完整或非目标扩展名移动至 error。
- 验证：见 [tests/test_ingress_atomic.py](tests/test_ingress_atomic.py:13)

3) 按 N1/N2/N3 分组三色（R/G/B）
- 目的：从 work 中聚合三色三合一任务组。
- 使用的代码：
  - 分组服务 [GroupingService.group()](app/business/grouping.py:25)
  - 分组实体 [GroupTriplet](app/business/grouping.py:12)
- 规则：文件名形如 A-B-N1/N2/N3，按 N1→R，N2→G，N3→B 映射，必须是 .txt+.jpg 成对。
- 验证：见 [tests/test_grouping.py](tests/test_grouping.py:33)

4) 映射与闪烁 attrs 合成
- 目的：解析各色文本得到 indices，按 R→G→B 顺序合并；根据蛇形映射与百分比阈值生成 attrs(bit0)。
- 使用的代码：
  - 解析索引+百分比 [parse_indices_and_percent_from_txt()](app/business/mapping.py:76)
  - 蛇形映射 [serpentine_map()](app/business/mapping.py:35)
  - 合成 indices/attrs [compose_indices_and_attrs_for_group()](app/business/mapping.py:107)
- 配置：
  - mapping.serpentine_enabled 与 mapping.cols 控制蛇形；
  - display.blink_enabled 与 display.blink_threshold_percent 控制闪烁标志。
- 验证：见 [tests/test_dispatcher_blink.py](tests/test_dispatcher_blink.py:60)

5) 派发与 A1 分片下发
- 目的：设备 B1 请求后，上位机 AF(OK) → 发送 A1（可能分片）→ 每片等待 BF。
- 使用的代码：
  - 派发器 [Dispatcher.request_next_payload()](app/business/dispatcher.py:35)
  - 会话分片发送 [SerialSession._send_a1_payload()](app/comm/session.py:296)
  - A1 构造 [build_a1()](app/comm/protocol.py:115)
- 典型接线：
  1. 创建 Dispatcher 指向 work_dir；
  2. 创建串口与会话 [SerialSession](app/comm/session.py:28)，并将 request_handler 设为 dispatcher.request_next_payload；
  3. 下位机发送 B1 后，会话按顺序响应。
- 验证：见 [tests/test_dispatcher.py](tests/test_dispatcher.py:48)、[tests/test_chunking.py](tests/test_chunking.py:43)

6) 心跳调度与在线判定
- 目的：周期发送 A0 判定在线，失败计数达到阈值置 OFFLINE，成功后恢复 CONNECTED。
- 使用的代码：
  - 发送心跳 [send_heartbeat()](app/comm/session.py:190)
  - 启动心跳线程 [start_heartbeat()](app/comm/session.py:194)、停止 [stop_heartbeat()](app/comm/session.py:221)
- 配置：comm.enable_heartbeat、comm.heartbeat_interval_seconds、comm.offline_failure_threshold。
- 验证：见 [tests/test_heartbeat_scheduler.py](tests/test_heartbeat_scheduler.py:66)

7) ACK 等待与重试
- 目的：对 A0/A1/AF 的 ACK 等待采用配置驱动的重试策略，失败计数用于离线判定。
- 使用的代码：
  - [send_and_wait_ack()](app/comm/session.py:139)
- 配置：comm.retry.enabled/ack_timeout_ms/max_attempts/backoff_ms。
- 验证：见 [tests/test_session_retry.py](tests/test_session_retry.py:65)

8) 日志与 HEX 捕获
- 目的：便于联调串口 TX/RX；控制日志级别、格式、轮转。
- 使用的代码：
  - 获取日志器 [get_logger()](app/logs/logger.py:24)、HEX 格式化 [hex_dump()](app/logs/logger.py:61)
- 配置：logging.level/file/format/rotate/* 与 logging.hex.capture/incoming/outgoing/max_bytes。
- 验证：见 [tests/test_logging_config.py](tests/test_logging_config.py:51)

9) 错误与幂等边界
- 重复 B1：仅回 AF(DUPLICATE)，不重复下发 A1，见 [_handle_frame()](app/comm/session.py:270)。
- 乱序 B1：小于 expected 回 0x03，大于 expected 回 0x04，且不派发，见 [_handle_frame()](app/comm/session.py:276)。
- 协议错误：未知 TYPE 或 CHECK 失败由会话层回 AF(code)，见 [decode_stream()](app/comm/protocol.py:62) 与 [_on_bytes()](app/comm/session.py:239)。

10) 打印标签（占位实现）
- 目的：将一组条目按列打印（当前用日志模拟，后续可接入真实热敏打印机）。
- 使用的代码：
  - [Printer](app/devices/printer.py:5)、[Printer.print_labels()](app/devices/printer.py:17)
- 配置：printing.enabled/columns/paper/*。

11) 真实串口适配（模板）
- 目的：替换 [SerialPortBase](app/comm/serial_port.py:4) 为实际串口实现（如 QtSerialPort 或 pyserial）。
- 关键接口：set_rx_callback(bytes->None)、open()/close()、write_bytes(bytes)。
- 注意：回调需线程安全；遵循协议帧与会话时序。

12) 变更映射布局（列数/蛇形）
- 目的：现场柜体列数或蛇形策略变更时的标准改法。
- 步骤：
  1. 在配置中调整 mapping.cols 与 mapping.serpentine_enabled；
  2. 如需固定顺序验证，可临时关闭蛇形以对齐人工检查；
  3. 运行一轮派发自检，确认顺序与下位机物理一致。
- 参考：蛇形实现 [serpentine_map()](app/business/mapping.py:35)

13) 调整闪烁阈值或开关
- 目的：根据实际用料“占比”调优闪烁提示敏感度。
- 步骤：
  1. 在配置中设置 display.blink_threshold_percent 与 display.blink_enabled；
  2. 保持文本行“index percent”格式；
  3. 验证 attrs(bit0) 生效：见 [compose_indices_and_attrs_for_group()](app/business/mapping.py:107)。

14) 批次推进与归档
- 目的：B1 拉取下一组后，在 BF(00) 成功后归档至 done 或错误分流至 error。
- 使用的代码：
  - [Dispatcher.archive_pending()](app/business/dispatcher.py:59)、[Dispatcher.archive_group()](app/business/dispatcher.py:44)
- 建议：以会话回调 on_a1_result 带动归档（代码已预留 [SerialSession.on_a1_result](app/comm/session.py:111)）。

15) 端到端自测（内存串口）
- 目的：不依赖真实硬件快速回归。
- 步骤：
  1. 使用 [FakeSerialPort](app/comm/serial_port.py:26) 建立 PC/MCU 对；
  2. 运行分发/心跳/重试相关测试用例；
  3. 手动构造 B1 帧并观察 AF/A1/BF 流。
- 参考用例：
  - [tests/test_dispatcher.py](tests/test_dispatcher.py:48)、[tests/test_session.py](tests/test_session.py:44)、[tests/test_session_retry.py](tests/test_session_retry.py:65)

附：常见问题与建议
- 入库文件半写：提升 ingress.ready_quiet_ms，并规范仅在 watch 为空时投放。
- 离线误判：区分心跳线程内“无重试”与显式 send_heartbeat() 的重试差异。
- A1 超长：利用会话分片，必要时下调 bytes_per_frame；注意 attrs 与 indices 对齐。
- 日志滚动：开发阶段可关闭 rotate.enabled，便于 grep HEX。
- 真实串口：首先以内存串口跑通流程，再替换适配层，降低集成风险。