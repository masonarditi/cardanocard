export default function PhonePreview() {
  return <figure className="iphone-preview" aria-label="Static iPhone preview of a Cardano Card shopping conversation">
    <div className="iphone-device">
      <span className="iphone-switch" aria-hidden="true" />
      <span className="iphone-volume volume-up" aria-hidden="true" />
      <span className="iphone-volume volume-down" aria-hidden="true" />
      <span className="iphone-power" aria-hidden="true" />
      <div className="iphone-rim" aria-hidden="true" />
      <div className="iphone-screen">
        <div className="iphone-island" aria-hidden="true"><span /></div>
        <div className="iphone-contact">
          <span className="iphone-avatar" aria-hidden="true">
            <svg viewBox="0 0 64 64" fill="none"><path d="M42 24a17 17 0 1 0 2 21" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" /><path d="m30 34 22-22M38 12h14v14" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </span>
          <span className="iphone-contact-name">Cardano Card <span aria-hidden="true">›</span></span>
        </div>
        <div className="iphone-messages">
          <p className="iphone-message received">Found Colombian coffee for $12.41. Want me to order it?</p>
          <p className="iphone-message sent">yes</p>
          <p className="iphone-message received">Your coffee is ordered. I’ll send the receipt and delivery updates right here.</p>
        </div>
      </div>
    </div>
  </figure>;
}
