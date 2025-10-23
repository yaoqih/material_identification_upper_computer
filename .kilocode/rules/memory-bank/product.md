# 产品说明 - Material Identification Upper Computer

目标与愿景
- 构建一个可靠的上位机应用，面向画布配色与拣料作业，使用串口与下位机通讯，控制 WS2812 灯带高亮提示仓格，降低人工误差并提升流水效率。
- 统一管理外部预处理输出的文本和图片文件，支持批次化分组、错误分流与归档，形成可追踪的生产流水。

为什么需要这个项目
- 传统人工拣料在多色多组的场景下容易出错，需要机器可视提示和标签化管理。
- 下位机硬件与蛇形布线的约束导致仓格到灯带的索引映射复杂，需要在上位机端预先计算好数据。
- 实际生产存在文件半写、批次插队、退单、断电恢复等情况，上位机需要提供稳健的数据流转与容错策略。

解决的问题与核心价值
- 文件入库与安全搬移：从共享的预处理目录拉取 .txt 与 .jpg 成对文件，使用安静窗口与原子剪切保障完整性，错误文件分流到 error 目录。[app/business/file_ingress.py](app/business/file_ingress.py)
- 分组与编队：按文件名末尾标签 N1/N2/N3 聚合为三色组，映射为 R/G/B，形成完整的三合一任务单。[app/business/grouping.py](app/business/grouping.py)
- 映射与合成：从文本提取仓格索引，按 R→G→B 顺序合并，支持蛇形(Z字形)重排以匹配物理布线；可选根据行内百分比生成闪烁标志 attrs(bit0)。[app/business/mapping.py](app/business/mapping.py)
- 派发与会话：设备发起 B1 请求时，上位机应答 AF(OK)，随后下发合成后的 A1 指令（支持分片），并等待 BF 确认；重复或乱序 B1 做幂等与错误码处理。[app/business/dispatcher.py](app/business/dispatcher.py)、[app/comm/session.py](app/comm/session.py)
- 协议一致性：遵循固定 HEADER、TYPE、LEN、SEQ、VAL、CHECK 的格式，提供 A0/A1/AF 与 B0/B1/BF 类型及错误应答码。[docs/通信约定.md](docs/通信约定.md)、[app/comm/protocol.py](app/comm/protocol.py)
- 心跳与在线判定：周期发送 A0 心跳，收到任何 BF 视为在线；连续失败达到阈值置 OFFLINE，成功后自动恢复 CONNECTED。[tests/test_heartbeat_scheduler.py](tests/test_heartbeat_scheduler.py)
- 日志与可观测性：支持十六进制 TX/RX 捕获开关、日志级别、文件轮转与格式可配置。[app/logs/logger.py](app/logs/logger.py)、[tests/test_logging_config.py](tests/test_logging_config.py)

目标用户与使用场景
- 产线拣料人员：根据灯带高亮与闪烁提示快速定位仓格，减少错误与时间成本。
- 流水线管理员：通过目录结构与归档记录掌握进度，必要时执行退单或替换。
- 系统维护者：通过日志与配置对通讯、重试、映射、显示策略进行调优。

工作流程（概述）
1. 预处理端将文本(.txt)与图片(.jpg/.jpeg)以同名对投放到共享的 watch 目录。
2. 上位机按安静窗口过滤半写文件，原子剪切入库到 work；不合规或不完整文件移至 error。
3. 在 work 中按 N1/N2/N3 聚合为一组三色任务，映射为 R、G、B，解析出仓格索引与可选百分比。
4. 按布局与配置执行蛇形映射，合成 A1 指令的 indices 列表与可选 attrs 闪烁掩码。
5. 下位机通过 B1 请求下一任务；上位机先以 AF(OK) 应答，再分片下发 A1，等待 BF 确认。
6. 成功后将该组三色文件归档至 done；失败或异常则分流到 error 并继续流水。
7. 周期心跳维持在线判定；离线达到阈值时标记 OFFLINE，恢复后自动清零并继续。

关键功能清单
- 串口通讯协议：A0/A1/AF 与 B0/B1/BF 帧结构与校验、SEQ 规则、错误码。
- 文件入库：安静窗口、原子剪切、就绪文件配对、错误分流。
- 分组与映射：N1/N2/N3→R/G/B；蛇形重排；indices 合成；attrs 闪烁标志。
- 派发与会话：B1 流程、分片、ACK 等待与重试、重复与乱序处理、在线状态机。
- 心跳调度：周期 A0、阈值离线与恢复。
- 日志与配置：十六进制捕获、级别/格式/轮转、APP_CONFIG_PATH 注入与默认填充。[app/storage/config.py](app/storage/config.py)
- 归档与追踪：成功归档至 done，失败分流至 error；支持继续生产与断电恢复。
- 打印对接（预留）：热敏打印机标签输出，两列版式，显示组别/文件名/条目编号与名称。[app/devices/printer.py](app/devices/printer.py)

用户体验目标
- 直观：灯带常亮表示标准拣料，超过阈值的颜色闪烁，便于二次确认。
- 迅捷：批次推进以按钮请求触发，自动编组与分片下发，减少等待。
- 可追踪：目录归档与日志可追踪当前进度，容错不中断流水。
- 可配置：通过 JSON 配置微调心跳、重试、映射与日志策略，适配不同产线。

兼容性与容错边界
- 相同 B1 重复请求仅回 AF(DUPLICATE)，不重复下发 A1；乱序请求返回 0x03/0x04 并拒绝派发。[app/comm/session.py](app/comm/session.py)
- 指令 A1 支持分片；当提供 attrs 时按 3 字节/项(index+flags)，否则为 2 字节/项；每片等待 BF。[app/comm/session.py](app/comm/session.py)、[app/comm/protocol.py](app/comm/protocol.py)
- 入库仅处理就绪且成对文件；不完整或不支持扩展名文件移至 error。[app/business/file_ingress.py](app/business/file_ingress.py)

与下位机的约定与扩展
- 闪烁控制通过 attrs 的 bit0 预留位表达；具体闪烁时序由下位机实现并在通讯协议中定义类型或模式扩展。[app/comm/protocol.py](app/comm/protocol.py)
- 指令包 VAL 中的索引使用十进制数编码为小端 uint16；不分组，集中下发；下位机按灯带序号控制。[docs/通信约定.md](docs/通信约定.md)

未来扩展
- 打印机驱动与版式模板管理；标签同步打印与派发回调绑定。
- 多端口/多设备管理与并发派发，完善 DeviceManager 与 UI。
- 更丰富的错误码与协议扩展：VAL/LEN 检查、attrs 多位含义定义（颜色/模式等）。
- 更严密的批次调度：退单与替换策略的流程化与审计。

参考与定位
- 通讯协议与示例：[docs/通信约定.md](docs/通信约定.md)
- 协议实现与校验：[app/comm/protocol.py](app/comm/protocol.py)
- 会话与心跳/重试：[app/comm/session.py](app/comm/session.py)
- 入库与分组：[app/business/file_ingress.py](app/business/file_ingress.py)、[app/business/grouping.py](app/business/grouping.py)
- 映射与闪烁：[app/business/mapping.py](app/business/mapping.py)
- 派发与归档：[app/business/dispatcher.py](app/business/dispatcher.py)
- 配置与日志：[app/storage/config.py](app/storage/config.py)、[app/logs/logger.py](app/logs/logger.py)
- 测试套件（行为契约）：[tests/test_dispatcher.py](tests/test_dispatcher.py)、[tests/test_dispatcher_blink.py](tests/test_dispatcher_blink.py)、[tests/test_session_retry.py](tests/test_session_retry.py)、[tests/test_heartbeat_scheduler.py](tests/test_heartbeat_scheduler.py)