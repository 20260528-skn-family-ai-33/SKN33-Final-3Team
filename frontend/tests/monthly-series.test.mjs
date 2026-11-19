import test from 'node:test'
import assert from 'node:assert/strict'
import { monthlySeries } from '../src/admin/monthlySeries.js'

test('monthly chart caps at 12 and never bridges unrecorded months', () => {
  const rows = Array.from({length:15}, (_,i) => ({month:String(i), total_members:i===13 ? null : i}))
  const result = monthlySeries(rows, 'total_members')
  assert.equal(result.points.length,12)
  assert.equal(result.points[0].month,'3')
  assert.equal(result.segments.length,2)
  assert.equal(result.segments[1][0].month,'14')
  assert.ok(result.max>=14)
})
test('zero is a real point and missing values stay missing', () => {
  const result = monthlySeries([{month:'a',total_members:null},{month:'b',total_members:0}], 'total_members')
  assert.equal(result.segments.length,1)
  assert.equal(result.segments[0][0].value,0)
  assert.ok(result.max>0)
  assert.equal(monthlySeries([], 'total_members').segments.length,0)
})
