import { Link } from "react-router-dom";
import { Skeleton, Tabs } from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { useAuth } from "../auth";
import { useGraphSpace } from "../graph-space";
import { ErrorNotice, PageHeader } from "../components/Common";
import { GraphExplorer } from "./GraphExplorer";
export function GraphSpacePage({ spaceId, onTab }: { spaceId: string; onTab: (tab: string) => void }) {
  const { api } = useAuth();
  const space = useGraphSpace(api, spaceId);
  return <>
    <Link className="back-link" to="/spaces"><ArrowLeftOutlined/> 全部空间</Link>
    {space.data && !space.error ? <PageHeader title={space.data.name} description="源文件是证据，Wiki 是正式知识；每次修改都保留版本与审核记录。"/> : space.loading ? <Skeleton.Input active style={{ width: 240 }}/> : <ErrorNotice error={space.error} retry={space.refresh}/>}
    <Tabs activeKey="graph" onChange={onTab} destroyOnHidden items={[
      { key: "wiki", label: "Wiki" }, { key: "sources", label: "数据来源" },
      { key: "graph", label: "知识图谱", children: <GraphExplorer spaceId={spaceId} available={!!space.data && !space.error}/> },
      { key: "activity", label: "活动" }, { key: "settings", label: "空间设置" },
    ]}/>
  </>;
}