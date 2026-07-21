type UpgradeBannerProps = {
  reason?: string
  onUpgrade: () => void
}

export function UpgradeBanner({ reason, onUpgrade }: UpgradeBannerProps) {
  return (
    <div className="upgrade-banner">
      <div className="upgrade-banner-content">
        <strong>You&apos;ve reached your message limit</strong>
        <span>
          {reason
            ? 'Your current plan has no messages left this period.'
            : 'Upgrade your plan to keep chatting.'}
        </span>
      </div>
      <button type="button" className="upgrade-banner-btn" onClick={onUpgrade}>
        See plans
      </button>
    </div>
  )
}
