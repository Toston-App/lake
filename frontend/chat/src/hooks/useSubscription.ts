import { useCallback, useEffect, useState } from 'react'
import * as subApi from '../api/subscriptions'
import { ApiError } from '../api/client'
import type {
  SubscriptionMeOut,
  ChangePlanOut,
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
  openCheckout: (tier: TierSlug) => Promise<ChangePlanOut | void>
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
    const task = window.setTimeout(() => {
      if (authenticated) {
        void refresh()
      } else {
        setData(null)
        setTiers([])
      }
    }, 0)
    return () => window.clearTimeout(task)
  }, [authenticated, refresh])

  const openCheckout = useCallback(async (tier: TierSlug) => {
    const status = data?.subscription.status
    if (status === 'active') {
      const result = await subApi.changePlan(tier)
      await refresh()
      return result
    }
    if (status === 'trialing' || status === 'past_due') {
      const { url } = await subApi.createPortalSession()
      window.location.href = url
      return
    }
    const { url } = await subApi.createCheckout(tier)
    window.location.href = url
  }, [data?.subscription.status, refresh])

  const openPortal = useCallback(async () => {
    const { url } = await subApi.createPortalSession()
    window.location.href = url
  }, [])

  const usage = data?.usage
  const isOverQuota =
    usage != null &&
    ((usage.messages_limit != null &&
      usage.messages_used >= usage.messages_limit) ||
      (usage.tokens_limit != null && usage.tokens_used >= usage.tokens_limit))

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
