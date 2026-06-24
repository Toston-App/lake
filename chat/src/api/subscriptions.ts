import { apiFetch } from './client'
import type {
  CheckoutOut,
  PortalOut,
  SubscriptionMeOut,
  TierOut,
  TierSlug,
} from '../types/subscription'

const PREFIX = '/api/v1/subscriptions'

export function fetchTiers(): Promise<TierOut[]> {
  return apiFetch<TierOut[]>(`${PREFIX}/tiers`)
}

export function fetchMySubscription(): Promise<SubscriptionMeOut> {
  return apiFetch<SubscriptionMeOut>(`${PREFIX}/me`)
}

export function createCheckout(tier: TierSlug): Promise<CheckoutOut> {
  return apiFetch<CheckoutOut>(`${PREFIX}/checkout`, {
    method: 'POST',
    body: JSON.stringify({ tier }),
  })
}

export function createPortalSession(): Promise<PortalOut> {
  return apiFetch<PortalOut>(`${PREFIX}/portal`, {
    method: 'POST',
  })
}
