export default function BrandMark({
  tagline = 'Premium logistics AI',
  compact = false,
  loading = false,
  showTagline = true,
  className = '',
}) {
  const classes = ['brand-shell']
  if (compact) {
    classes.push('brand-shell--compact')
  }
  if (className) {
    classes.push(className)
  }

  const markClasses = ['brand-mark']
  if (loading) {
    markClasses.push('brand-mark--loading')
  }
  if (compact) {
    markClasses.push('brand-mark--compact')
  }

  return (
    <div className={classes.join(' ')}>
      <span className={markClasses.join(' ')} aria-hidden="true">
        <span className="brand-mark__glyph">SK</span>
        <span className="brand-mark__halo" />
      </span>

      {showTagline && (
        <span className="brand-copy">
          <strong>SKDLS Transport AI</strong>
          <span>{tagline}</span>
        </span>
      )}
    </div>
  )
}
