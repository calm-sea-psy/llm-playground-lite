// 선택한 실행의 재채점 미리보기 문구 — 무엇이 바뀌는지는 백엔드가 계산하고, 여기서는 읽을 문장만 만든다.

const FILE_KIND = { run: '원래 실행', metric_rerun: '지표 재실행' }

function shortTime(iso) {
  if (!iso) return '시각 기록 없음'
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const willChange = (m) => m.rescorable && (m.state === 'rescore' || m.state === 'unrecorded')

/** 재채점할 파일 수, 그 파일들에서 버전이 바뀌는 지표 수, 결과 버전이 코드보다 높아 막은 파일 수. */
export function rescoreSummary(plan) {
  const files = (plan?.runs ?? []).flatMap((r) => r.files)
  const toRescore = files.filter((f) => f.will_rescore)
  return {
    files: toRescore.length,
    metrics: toRescore.reduce((n, f) => n + f.metrics.filter(willChange).length, 0),
    ahead: files.filter((f) => f.blocked && f.metrics.some((m) => m.state === 'ahead' && m.rescorable)).length,
  }
}

// 키 순서와 무관한 같다/다르다 — 버전 기록은 객체라 문자열로 바로 견주면 키 순서에 흔들린다
function sameValue(a, b) {
  const canon = (v) =>
    v && typeof v === 'object' && !Array.isArray(v)
      ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon(v[k])]))
      : Array.isArray(v)
        ? v.map(canon)
        : v
  return JSON.stringify(canon(a ?? null)) === JSON.stringify(canon(b ?? null))
}

/** 서버가 읽은 실행이 화면이 들고 있는 상세와 다른가 — 붙은 재실행이나 버전 기록이 달라졌으면 다르다.
 * 화면이 아직 상세를 받지 않았으면 대조하지 않는다. */
function runChanged(run, held) {
  if (!held || run.missing) return false
  return !sameValue(run.rerun_ids ?? [], held.rerun_ids ?? []) || !sameValue(run.scorer_versions, held.scorer_versions)
}

/** 서버가 읽은 기준선이 화면 참고선의 기준선과 다른가 — 새로 측정해 다른 파일이 됐거나, 재채점으로 버전 기록이 바뀌었다.
 * `held`는 화면의 기준선 응답(`{run, staleness}`)이고 아직 받지 않았으면(null) 대조하지 않는다. */
function baselineChanged(base, held) {
  if (!held) return false
  const run = held.run ?? null
  if (!base || !run) return Boolean(base) !== Boolean(run)
  return base.run_id !== run.id || !sameValue(base.scorer_versions, run.scorer_versions)
}

/** 비교 화면에 띄울 줄 — 재채점으로 풀리는 낡음, 재채점하면 안 되는 파일(코드가 되돌아갔을 수 있음),
 * 그리고 재채점으로 풀리지 않는 낡음(재측정 필요). 마지막 것은 버튼으로 꺼지지 않지만 리포트 표지와 같은 사실이라
 * 화면에서도 띄운다 — 화면에 보이는 값만 센다(재실행에 가려진 값은 사람이 확인할 수 없다).
 * 같은 경고 칸 색이라 풀리는지 아닌지는 문구가 가른다. `nameOf`는 실행 → 화면에 쓰는 이름.
 *
 * `held`(`{runs: 실행 id → 화면의 상세, baseline: 화면의 기준선 응답}`)를 주면 서버가 읽은 것과 대조한다.
 * 다르면 그 실행·기준선의 버전 줄을 지우고 **갱신 사실**을 띄운다 — 새로 읽은 값은 아직 화면에 없어서,
 * 그것으로 경고를 지우면 경고가 화면에 없는 것을 설명하게 된다. */
export function rescoreNotices(plan, nameOf = (run) => run.model ?? run.run_id, held = null, { cli = true } = {}) {
  const changed = new Set((plan?.runs ?? []).filter((r) => held?.runs && runChanged(r, held.runs[r.run_id])).map((r) => r.run_id))
  const baseChanged = Boolean(held) && baselineChanged(plan?.baseline, held.baseline)
  const lines = [
    ...(plan?.runs ?? [])
      .filter((r) => changed.has(r.run_id))
      .map((r) => `${nameOf(r)}의 결과가 갱신됐다 — 새로고침하면 표의 점수가 따라온다`),
    ...(baseChanged ? ['기준선이 갱신됐다 — 새로고침하면 참고선과 표지가 따라온다'] : []),
  ]
  if (plan && (changed.size || baseChanged)) {
    plan = { ...plan, runs: plan.runs.filter((r) => !changed.has(r.run_id)), baseline: baseChanged ? null : plan.baseline }
  }
  const s = rescoreSummary(plan)
  if (s.files > 0) lines.push(`채점기 버전이 낡은 값이 있다 — 재채점하면 풀린다 (파일 ${s.files}개 · 지표 ${s.metrics}개)`)
  if (s.ahead > 0) {
    lines.push(`결과의 채점기 버전이 지금 코드보다 높은 파일 ${s.ahead}개 — 코드가 되돌아갔을 수 있다. 재채점하지 말 것`)
  }
  for (const run of plan?.runs ?? []) {
    const remeasure = run.files.flatMap((f) => f.metrics.filter((m) => m.state === 'remeasure' && m.shown))
    if (remeasure.length) {
      const items = remeasure.map((m) => `${m.label} (${m.change})`).join('; ')
      lines.push(`채점기 버전이 낡았다 — 재측정 필요(재채점으로 풀리지 않는다) · ${nameOf(run)}: ${items}`)
    }
  }
  lines.push(...baselineNotices(plan?.baseline, cli))
  return lines
}

/** 기준선(참고선) 줄 — 버튼 대상이 아니므로 권하는 행동이 다르다. 표지처럼 낡음·앞섬만 경고로 띄우고
 * 기록 없음은 띄우지 않는다(모르는 것이지 믿으면 안 되는 것이 아니다). */
function baselineNotices(base, cli) {
  if (!base) return []
  const name = `기준선(${base.model})`
  const of = (state) => base.metrics.filter((m) => m.state === state).map((m) => `${m.label} (${m.change})`)
  const lines = []
  if (of('ahead').length) {
    lines.push(`결과의 채점기 버전이 지금 코드보다 높다 — 코드가 되돌아갔을 수 있다. 재채점하지 말 것 · ${name}: ${of('ahead').join('; ')}`)
  }
  if (of('rescore').length) {
    // 기준선은 화면에서 재채점하지 않는다 — CLI가 없는 서버에서는 다시 재는 것만 남는다
    const how = cli ? '재채점 필요(기준선은 CLI로 재채점한다: maintenance.py rescore)' : '다시 측정 필요(기준선은 화면에서 재채점하지 않는다)'
    lines.push(`채점기 버전이 낡았다 — ${how} · ${name}: ${of('rescore').join('; ')}`)
  }
  if (of('remeasure').length) {
    lines.push(`채점기 버전이 낡았다 — 재측정 필요(재채점으로 풀리지 않는다) · ${name}: ${of('remeasure').join('; ')}`)
  }
  return lines
}

/** 실행 하나의 출처 파일 구성 — `파일 3개 (원래 실행 + 지표 재실행 2개)`. */
export function runFilesText(run) {
  const reruns = run.files.filter((f) => f.kind === 'metric_rerun').length
  return reruns ? `파일 ${run.files.length}개 (원래 실행 + 지표 재실행 ${reruns}개)` : `파일 ${run.files.length}개`
}

export function fileLabel(file) {
  const kind = FILE_KIND[file.kind] ?? file.kind
  const note = file.blocked ? ` — ${file.blocked}` : file.will_rescore ? '' : ' — 바뀌는 버전 없음'
  return `${kind} (${shortTime(file.started_at)})${note}`
}

/** 파일 안 지표 하나의 변화. 재채점으로 풀리지 않는 것과 화면에 안 보이는 값도 그대로 말한다. */
export function metricLine(m) {
  const tail = []
  if (m.state === 'ahead') tail.push('재채점하지 않는다')
  if (m.state === 'remeasure') tail.push('재채점으로 풀리지 않는다 — 재측정 필요')
  if (!m.shown) tail.push('이 파일의 값은 재실행에 가려져 화면에 보이지 않는다')
  return [`${m.label} — ${m.change}`, ...tail].join(' · ')
}
