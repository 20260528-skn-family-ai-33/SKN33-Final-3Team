import { Fragment } from 'react'
import { referenceParts } from './articlePresentation'

export default function ReferenceText({ text }) {
  return referenceParts(text).map((part, index) => <Fragment key={index}>
    {part.reference && <><span className="tag">참고</span>{' '}</>}{part.text}
  </Fragment>)
}
