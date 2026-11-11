export function taskTitle(task, tables) {
  const flow = { EXPORT: '수출', IMPORT: '수입' }[task.conditions?.trade_flow]
  if (task.intent !== 'STATISTICS' || !flow || !tables.some(table => table.meta?.kind === 'TREND')) {
    return task.question
  }

  const directions = /수출입|수입출|수출\s*(?:및|과|·|ㆍ|\/|,|와)\s*수입|수입\s*(?:및|과|·|ㆍ|\/|,|와)\s*수출|수출|수입/g
  const matches = task.question.match(directions) || []
  // 여러 방향을 별도로 언급한 질문은 문장을 보존하고 이 결과의 방향을 표시한다.
  return matches.length === 1 ? task.question.replace(directions, flow) : `${flow} · ${task.question}`
}
