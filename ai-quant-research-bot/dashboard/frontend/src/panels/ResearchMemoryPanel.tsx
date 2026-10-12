import type { ResearchMemory, ResearchMemoryPaper } from "../types";
import { Empty, Panel } from "./HealthPanel";

export function ResearchMemoryPanel({ data }: { data: ResearchMemory | null }) {
  if (!data || data.papers.length === 0) {
    return (
      <Panel title="Research Scholar - Paper Memory">
        <Empty />
      </Panel>
    );
  }

  return (
    <Panel title="Research Scholar - Paper Memory">
      <div className="panel-footnote">{data.papers.length} papers recorded (src/research/paper_memory.py) - deduped by DOI / arXiv id / title</div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Year</th>
            <th>Citations</th>
            <th>Evidence</th>
            <th>Strategies</th>
            <th>Hypotheses</th>
            <th>Sources</th>
          </tr>
        </thead>
        <tbody>
          {[...data.papers].reverse().map((paper) => (
            <PaperRow key={paper.paper_key} paper={paper} />
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function PaperRow({ paper }: { paper: ResearchMemoryPaper }) {
  const titleCell = paper.url ? (
    <a href={paper.url} target="_blank" rel="noreferrer">
      {paper.title}
    </a>
  ) : (
    paper.title
  );

  return (
    <tr>
      <td>{titleCell}</td>
      <td>{paper.year ?? "-"}</td>
      <td>{paper.citation_count ?? "-"}</td>
      <td>
        {paper.evidence_score
          ? `${paper.evidence_score.overall_score.toFixed(2)} (${paper.evidence_score.citation_tier})`
          : "unscored"}
      </td>
      <td>{paper.evidence_score?.matched_strategies.join(", ") || "-"}</td>
      <td>{paper.hypothesis_ids.length > 0 ? paper.hypothesis_ids.join(", ") : "-"}</td>
      <td>{paper.sources.join(", ")}</td>
    </tr>
  );
}
