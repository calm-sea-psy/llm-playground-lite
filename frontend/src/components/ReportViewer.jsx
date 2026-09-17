// 내보낸 리포트를 페이지 안에서 본다 — 화면용으로 따로 그리지 않고 저장한 파일을 그대로 연다.
// 내보낸 뒤 선택·가중치·판정을 바꿨으면 그 파일은 지금 화면과 다른 조건이라, 닫지 않고 그렇다고 적는다.
export default function ReportViewer({ saved, stale, onClose }) {
  return (
    <section className="report-viewer">
      <div className="report-viewer-head">
        <span className="report-viewer-name">{saved.saved_to}</span>
        <div className="report-viewer-actions">
          <a href={saved.view_url} target="_blank" rel="noreferrer">
            새 탭에서 열기
          </a>
          {saved.transcripts_view_url ? (
            <a href={saved.transcripts_view_url} target="_blank" rel="noreferrer">
              응답 전문 열기
            </a>
          ) : (
            saved.transcripts_note && <span className="report-viewer-note">응답 전문 없음: {saved.transcripts_note}</span>
          )}
          <button type="button" className="ghost" onClick={onClose}>
            닫기
          </button>
        </div>
      </div>
      {stale && (
        <div className="compare-warning">
          이 파일은 지금 화면과 다른 조건(선택·가중치·판정)으로 내보낸 것입니다 — 다시 내보내면 지금 조건으로 바뀝니다.
        </div>
      )}
      <iframe className="report-viewer-frame" title={`리포트 ${saved.saved_to}`} src={saved.view_url} />
    </section>
  )
}
