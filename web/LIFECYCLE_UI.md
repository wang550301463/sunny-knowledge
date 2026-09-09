# Wiki 生命周期界面

当前 Wiki 正文与固定历史修订页显示以下独立维度：不同登记来源数、事实声明的原始证据覆盖比例、有效状态、新鲜度，以及本人在当前 ACL 域内的近 7 天／30 天访问次数和最近访问时间。登记来源数不表示统计独立或结论正确率；摘要不是新的原始证据；访问不更新新鲜度基准。

## 读取与授权

- 主正文读取依次请求 Wiki／精确修订及该修订的 `GET /api/v1/pages/{page_id}/lifecycle?revision_id=...`，在两者成功且页、修订、策略、范围与字段类型通过校验后一起显示正文和指标。
- 页面可见时每 5 秒复核。正在进行的慢请求会被合并，整个联合读取最多 10 秒；超时或任何读取错误立即撤回正文、指标、编辑／历史对话框与引用抽屉。失败后由显式重试、刷新或焦点恢复重新读取，避免自动重试持续闪现旧内容。
- 失去可见性立即清除页面内容并停止请求；重新可见、窗口恢复焦点或显式刷新时先清除，再授权。迟到结果通过请求序号和中止信号隔离。
- Wiki 打开的原文引用请求有独立 10 秒总时限，已知失败同样使整个 Wiki 撤回。`CitationDrawer.onFailure` 是可选接口，其他页面的调用行为保持不变。

## 本人访问事件

只有当前或固定修订的主正文实际挂载并通过两个浏览器绘制帧、页面仍可见、且已有登录用户时，才提交 `POST /api/v1/pages/{page_id}/access-events`。历史列表和差异面板没有这个触发器；预取、图谱、Agent、MCP 和渠道视图不接入它。

访问标识按认证会话、路由历史项、页和精确修订生成 SHA-256 存储键，值仅含随机幂等标识和成功布尔标志。`sessionStorage` 不保存正文、指标、令牌或原始用户／资源标识。相同历史项的 StrictMode、重挂载与页面刷新复用标识；实际新导航、修订或账号使用新标识。成功标志只控制重复计数，不能跳过任何授权 GET。

成功 POST 后重新读取指标而不重复 POST。未知发送结果保留原标识，重试仍用同一键；存储不可用或记录损坏时暂停新计数并提示，不换新键冒险重复。浏览器事件不是人工阅读证明；后端仍验证 Web 客户端、当前用户及完整资源权限。

## 验证记录

2026-09-10 本地定向回归：5 个文件，46 项通过，32.01 秒，0 跳过。新增 32 项（生命周期 DTO／截止时间 9 项，存储与幂等 5 项，Wiki 显示／授权 18 项），另运行原 pinned revision 6 项和 live authorization 8 项。原编辑、审核、历史、回滚权限断言保留，仅补齐生命周期真实 DTO 与新增 GET 预期。

覆盖 StrictMode、重挂载、指标刷新不增量计数、隐藏页、未知 POST 重用键、当前与精确历史版本、部门／撤权返回的 403／503、焦点与迟到响应、慢复核不被定时器饿死、10 秒超时，以及原文失败／超时对整页的撤回。

命令：

```sh
./node_modules/.bin/vitest run src/test/lifecycle-api.test.ts src/test/lifecycle-display.test.ts src/test/lifecycle-wiki.test.tsx src/test/pinned-revisions.test.tsx src/test/live-authorization.test.tsx --maxWorkers=1 --no-file-parallelism
```

日志在 `knowledge-docker/artifacts/lifecycle-web-final-targeted.log`。类型检查和限定文件 ESLint 在该增量验证中通过；整个 Web 套件、构建及真实部署浏览器链路由集成阶段统一执行，这些 DOM／HTTP 协议测试不替代真实浏览器验收。
