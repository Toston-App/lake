import { useCallback, useEffect, useState } from 'react'
import * as subApi from '../api/subscriptions'
import { ApiError } from '../api/client'
import type {
  SubscriptionMeOut,
  TierOut,
  TierSlug,
} from '../types/subscription'

export type SubscriptionState = {
  loading: boolean
  data: SubscriptionMeOut | null
  tiers: TierOut[]
  error: string | null
  /** True when the user has used their entire message allowance. */
  isOverQuota: boolean
  refresh: () => Promise<void>
  openCheckout: (tier: TierSlug) => Promise<void>
  openPortal: () => Promise<void>
}

export function useSubscription(authenticated: boolean): SubscriptionState {
  const [data, setData] = useState<SubscriptionMeOut | null>(null)
  const [tiers, setTiers] = useState<TierOut[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [me, tiersList] = await Promise.all([
        subApi.fetchMySubscription(),
        subApi.fetchTiers(),
      ])
      setData(me)
      setTiers(tiersList)
    } catch (err) {
      const message =
        err instanceof ApiError ? err.detail : 'Failed to load subscription'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (authenticated) {
      void refresh()
    } else {
      setData(null)
      setTiers([])
    }
  }, [authenticated, refresh])

  const openCheckout = useCallback(async (tier: TierSlug) => {
    const { url } = await subApi.createCheckout(tier)
    window.location.href = url
  }, [])

  const openPortal = useCallback(async () => {
    const { url } = await subApi.createPortalSession()
    window.location.href = url
  }, [])

  const usage = data?.usage
  const isOverQuota =
    usage != null &&
    usage.messages_limit != null &&
    usage.messages_used >= usage.messages_limit

  return {
    loading,
    data,
    tiers,
    error,
    isOverQuota,
    refresh,
    openCheckout,
    openPortal,
  }
}
