# 技术说明 - Material Identification Upper Computer

技术栈与运行环境
- 语言：Python 3.10+（建议）
- 依赖：
  - PySide6≥6.6.0（预留 UI；当前代码未直接使用）
  - pytest≥8.0.0（测试）
  - pyinstaller≥6.0.0（打包）
- 入口：通过 [main()](app/main.py:6) 启动脚手架；当前仅输出启动信息。

核心库与内部模块
- 配置仓库：[ConfigRepo](app/storage/config.py:7)、[load()](app/storage/config.py:13)、[_apply_defaults()](app/storage/config.py:22)
- 日志模块：[get_logger()](app/logs/logger.py:24)、[hex_dump()](app/logs/logger.py:61)
- 协议层：[FrameType](app/comm/protocol.py:16)、[AckCode](app/comm/protocol.py:24)、[encode_frame()](app/comm/protocol.py:49)、[decode_stream()](app/comm/protocol.py:62)、[build_a0()](app/comm/protocol.py:112)、[build_a1()](app/comm/protocol.py:115)、[build_af()](app/comm/protocol.py:134)
- 会话层：[SerialSession](app/comm/session.py:28)、[send_heartbeat()](app/comm/session.py:190)、[start_heartbeat()](app/comm/session.py:194)、[stop_heartbeat()](app/comm/session.py:221)、[send_and_wait_ack()](app/comm/session.py:139)、[_send_a1_payload()](app/comm/session.py:296)
- 串口抽象：[SerialPortBase](app/comm/serial_port.py:4)、[FakeSerialPort](app/comm/serial_port.py:26)
- 设备管理：[DeviceManager](app/comm/device_manager.py:9)、[attach()](app/comm/device_manager.py:13)、[broadcast_heartbeat()](app/comm/device_manager.py:18)

配置系统
- 默认配置位于 [configs/default.json](configs/default.json)，可通过环境变量 APP_CONFIG_PATH 指向自定义 JSON 以覆盖默认。
- 运行时 [ConfigRepo.load()](app/storage/config.py:13) 会再次读取 APP_CONFIG_PATH，支持测试/运行过程动态注入。
- 主要配置项结构：
  - serial：端口/波特率/数据位/校验/停止位/超时/重试次数
  - grouping：watch_dir/work_dir/error_dir/done_dir
  - ingress：ready_quiet_ms（安静窗口毫秒）
  - mapping：rows/cols/snake 或 serpentine_enabled/start_corner/cabinets
  - printing：enabled/device/paper(width_mm,height_mm)/columns/rows/triple
  - display：blink_enabled/blink_threshold_percent
  - thresholds：percent_blink（用于兼容旧配置，[_apply_defaults()](app/storage/config.py:22) 会迁移为 display.blink_threshold_percent）
  - logging：level/file/format/rotate(max_bytes,backup_count)/hex(capture,incoming,outgoing,max_bytes)
  - session：heartbeat_interval_sec/offline_threshold（兼容旧键）
  - comm：enable_heartbeat/heartbeat_interval_seconds/offline_failure_threshold/retry(enabled,ack_timeout_ms,max_attempts,backoff_ms)
- 配置默认填充细节见：[_apply_defaults()](app/storage/config.py:22)

日志与可观测性
- 通过 [get_logger()](app/logs/logger.py:24) 获取命名日志器；使用 RotatingFileHandler 或 FileHandler 输出到文件与控制台。
- HEX 捕获：受 logging.hex.capture 控制；incoming/outgoing/max_bytes 进一步细化。会话层在收发处打印 [TX HEX/RX HEX]，参见 [SerialSession._send_frame](app/comm/session.py:123) 与 [SerialSession._on_bytes](app/comm/session.py:229)。
- 行为验证见 [tests/test_logging_config.py](tests/test_logging_config.py)。

协议与帧格式
- 帧布局：HEADER(4)+TYPE(1)+LEN(2)+SEQ(2)+VAL(N)+CHECK(1)，小端；HEADER 固定 F2 F8 F1 F2。
- CHECK=TYPE+LEN(LE)+SEQ(LE)+VAL 求和低8位；实现：[encode_frame()](app/comm/protocol.py:49)。
- 流式解码与错误回 AF：解析失败或未知 TYPE 时，会话层触发 [build_af()](app/comm/protocol.py:134) 回复错误码，参见 [decode_stream()](app/comm/protocol.py:62) 与 [SerialSession._on_bytes](app/comm/session.py:239)。

会话与重试/心跳/分片
- B1→AF(OK)→A1：设备请求后，上位机先 AF 再 A1；重复 B1 仅回 AF(DUPLICATE)；乱序返回 0x03/0x04。参见 [_handle_frame()](app/comm/session.py:250)。
- ACK 等待重试：调用 [send_and_wait_ack()](app/comm/session.py:139)，由 comm.retry.* 配置驱动；心跳线程关闭重试保持节奏。
- 心跳调度：构造时按 comm.enable_heartbeat 自动 [start_heartbeat()](app/comm/session.py:194)；在线判定以任意 BF 成功为准，失败达阈值置 OFFLINE，成功后恢复 CONNECTED。
- 分片与 attrs 对齐：[_send_a1_payload()](app/comm/session.py:296) 会按 bytes_per_frame 切片；当 attrs 存在时每项 3B(index+flags)，否则 2B；每片等待 BF。

串口与设备抽象
- [SerialPortBase](app/comm/serial_port.py:4) 提供 set_rx_callback/open/close/write_bytes；[FakeSerialPort](app/comm/serial_port.py:26) 用于测试内存环路连接 connect_peer。
- [DeviceManager.attach()](app/comm/device_manager.py:13) 为多设备管理预留扩展点。
- 实际串口实现可在后续替换为 PySide6 或其他 UI/驱动层的适配。

打印输出（占位实现）
- [Printer](app/devices/printer.py:5) 以 logging 方式模拟打印；由 printing.enabled/columns 控制；便于后续替换为真实热敏打印机驱动。

数据目录与入库流水
- 目录结构位于 [configs/default.json](configs/default.json) 的 grouping.*；watch→work/error 的原子剪切由 [FileIngressService.ingest_batch()](app/business/file_ingress.py:36) 与 [_safe_move()](app/business/file_ingress.py:115) 实现；安静窗口 ready_quiet_ms 过滤半写。
- 分组与映射：见 [GroupingService.group()](app/business/grouping.py:25) 与 [MappingService.compose_indices_and_attrs_for_group()](app/business/mapping.py:107)。
- 派发：见 [Dispatcher.request_next_payload()](app/business/dispatcher.py:35)。

测试与质量保障
- 使用 pytest，详见 [tests/](tests/)；关键契约：
  - 协议正确性与错误处理：[tests/test_protocol.py](tests/test_protocol.py)、[tests/test_protocol_errors.py](tests/test_protocol_errors.py)
  - 会话时序与心跳/重试/乱序/幂等：[tests/test_session.py](tests/test_session.py)、[tests/test_session_seq_errors.py](tests/test_session_seq_errors.py)、[tests/test_session_retry.py](tests/test_session_retry.py)、[tests/test_heartbeat_scheduler.py](tests/test_heartbeat_scheduler.py)
  - 入库/分组/分片/闪烁/日志：[tests/test_ingress_atomic.py](tests/test_ingress_atomic.py)、[tests/test_grouping.py](tests/test_grouping.py)、[tests/test_dispatcher.py](tests/test_dispatcher.py)、[tests/test_chunking.py](tests/test_chunking.py)、[tests/test_dispatcher_blink.py](tests/test_dispatcher_blink.py)、[tests/test_logging_config.py](tests/test_logging_config.py)

开发与运行建议
- 运行：`python -m app.main` 或直接执行 [main()](app/main.py:6)。
- 配置切换：export/set APP_CONFIG_PATH 指向自定义 JSON；建议将心跳/重试阈值在测试环境中调小以加快反馈。
- 日志：确保 logging.file 所在目录可写；rotate.enabled 可在开发时关闭便于查看。
- 数据：确保 data/* 目录存在且可写；程序会按需创建。

打包建议
- 使用 PyInstaller 生成可分发二进制（参考 requirements）；因含后台线程，建议在 UI 集成后统一打包流程。

已知限制与边界
- 当前仅提供 FakeSerialPort；实际串口未集成，需替换适配层。
- A1 attrs 的 bit0 定义为闪烁，其他位尚未定义；与下位机协同扩展。
- 文档中的 LEN/VAL 错误码未在 [AckCode](app/comm/protocol.py:24) 中全面使用，仅保留 UNKNOWN_TYPE/SEQ/CHECK 等核心码以符合现有实现与测试。

外部依赖清单
- 参见 [requirements.txt](requirements.txt)。