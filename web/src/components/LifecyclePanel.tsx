import { useEffect, useState } from "react";
import type { RefObject } from "react";
import { useLocation } from "react-router-dom";
import { Alert, Button, Descriptions, Space, Typography } from "antd";
import { useAuth } from "../auth";
import type { LifecycleMetrics } from "../lifecycle-types";
import { withLifecycleDeadline } from "../lifecycle-api";
import { DisplayStorageUnavailable, displayReceipt, lifecycleActor, recordDisplay } from "../lifecycle-display";
import { formatDate, StatusTag } from "./Common";

export function LifecyclePanel({ metrics, bodyRef, onRefresh, onRecorded, onInvalidate }: {
  metrics: LifecycleMetrics;
  bodyRef: RefObject<HTMLDivElement | null>;
  onRefresh: () => void;
  onRecorded: () => void;
  onInvalidate: (error: unknown) => void;
}) {
  const { api, user } = useAuth();
  const actor = lifecycleActor(user), location = useLocation();
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    if (!actor || document.visibilityState !== "visible") return;
    const controller = new AbortController();
    let frame = 0;
    const displayed = () => document.visibilityState === "visible" && !!bodyRef.current?.isConnected && !bodyRef.current.closest("[hidden], [aria-hidden='true']");
    // Two frames ensure a committed visible body has passed a browser paint boundary.
    frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => {
      void (async () => {
        try {
          if (!displayed() || controller.signal.aborted) return;
          const saved = await displayReceipt(actor, location.key, metrics.page_id, metrics.revision_id);
          if (saved.receipt.recorded || controller.signal.aborted || !displayed()) return;
          await withLifecycleDeadline(signal => recordDisplay(api, metrics.page_id, metrics.revision_id, saved.receipt.id, signal), controller.signal);
          if (controller.signal.aborted || !displayed()) return;
          saved.mark(); onRecorded();
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof DisplayStorageUnavailable) setUnavailable(true);
          else onInvalidate(error);
        }
      })();
    }); });
    return () => { controller.abort(); cancelAnimationFrame(frame); };
  }, [api, actor, location.key, metrics, bodyRef, onRecorded, onInvalidate]);
  const facts = metrics.support.fact_claim_count, supported = metrics.support.fact_claims_with_original_evidence;
  return <section aria-label="知识生命周期">
    <Space><Typography.Title level={5}>知识生命周期</Typography.Title><Button size="small" onClick={onRefresh}>刷新指标</Button></Space>
    <Descriptions column={1} size="small" items={[
      { key: "sources", label: "不同登记来源数", children: metrics.support.registered_source_count },
      { key: "coverage", label: "事实有原始依据", children: facts ? `${supported} / ${facts}（${Math.round(supported / facts * 100)}%）` : "暂无事实声明" },
      { key: "state", label: "有效状态", children: <><StatusTag status={metrics.validity.state} />{!metrics.validity.eligible && <span>当前检索不可用</span>}</> },
      { key: "freshness", label: "新鲜度", children: metrics.freshness.score === null ? "未知（无可用原始时间依据）" : `${Math.round(metrics.freshness.score * 100)}%` },
      { key: "baseline", label: "原始来源登记时间", children: metrics.freshness.observed_at ? formatDate(metrics.freshness.observed_at) : "未知" },
      { key: "seven", label: "我的近 7 天访问次数", children: metrics.access.mine_visits_7d },
      { key: "thirty", label: "我的近 30 天访问次数", children: metrics.access.mine_visits_30d },
      { key: "last", label: "我的最近访问", children: metrics.access.mine_last_accessed_at ? formatDate(metrics.access.mine_last_accessed_at) : "尚无记录" },
    ]} />
    <Typography.Paragraph type="secondary">按登记来源去重，不代表统计独立或结论正确。摘要不增加原始来源数量。新鲜度按原始登记时间计算，访问次数不会提高它。</Typography.Paragraph>
    <Typography.Paragraph type="secondary">访问次数仅属于我在当前权限范围下查看此版本的记录；刷新指标不会增加访问。</Typography.Paragraph>
    {unavailable && <Alert type="warning" message="无法保存本次访问标识，未记录访问次数。" />}
  </section>;
}