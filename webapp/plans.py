"""Fixed subscription tiers, shown as pricing cards on /payment. Not
user-editable — same static-registry pattern as admin_registry.py."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Plan:
    key: str
    name: str
    duration_days: int
    price_fcfa: int
    quota: int
    features: tuple[str, ...]
    highlight: str | None = None


PLANS: dict[str, Plan] = {
    p.key: p
    for p in (
        Plan(
            key="free",
            name="Free",
            duration_days=30,
            price_fcfa=0,
            quota=20,
            features=(
                "20 distinct matchups / month",
                "All markets: WDL, double chance, BTTS, over/under, scorelines",
                "Full prediction history",
                "No payment required",
            ),
        ),
        Plan(
            key="monthly",
            name="Monthly",
            duration_days=30,
            price_fcfa=10_000,
            quota=150,
            features=(
                "150 distinct matchups / month",
                "All markets: WDL, double chance, BTTS, over/under, scorelines",
                "Full prediction history",
            ),
        ),
        Plan(
            key="semiannual",
            name="6 Months",
            duration_days=182,
            price_fcfa=48_000,
            quota=1_200,
            features=(
                "1,200 distinct matchups (200/month)",
                "All markets: WDL, double chance, BTTS, over/under, scorelines",
                "Full prediction history",
                "20% cheaper than paying monthly",
            ),
            highlight="Most popular",
        ),
        Plan(
            key="annual",
            name="Yearly",
            duration_days=365,
            price_fcfa=84_000,
            quota=3_000,
            features=(
                "3,000 distinct matchups (250/month)",
                "All markets: WDL, double chance, BTTS, over/under, scorelines",
                "Full prediction history",
                "30% cheaper than paying monthly — best value",
            ),
            highlight="Best value",
        ),
    )
}

# The free plan is granted automatically (see auth.get_subscription /
# customer.verify_email_submit) rather than bought — the payment page only
# ever offers the tiers a user can actually pay for.
PAID_PLANS: tuple[Plan, ...] = tuple(p for p in PLANS.values() if p.price_fcfa > 0)
