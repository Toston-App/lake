type UpgradeBannerProps = {
  reason?: string
  onUpgrade: () => void
}

export function UpgradeBanner({ reason, onUpgrade }: UpgradeBannerProps) {
  return (
    <div className="upgrade-banner">
      <div className="upgrade-banner-content">
        <strong>You&apos;ve reached your usage limit</strong>
        <span>
          {reason
            ? 'You have used 100% of your plan this period.'
            : 'Upgrade your plan to keep chatting.'}
        </span>
      </div>
      <button type="button" className="upgrade-banner-btn" onClick={onUpgrade}>
        See plans
      </button>
    </div>
  )
}
