"""
DATA ENGINE — Master Seed Runner
Runs all seed scripts in order.
Run with: python -m database.seeds.seed_all
"""

import asyncio

from database.seeds.seed_tenants import seed_tenants


async def run_all_seeds() -> None:
    print("Starting database seeding...")
    print("─" * 40)

    await seed_tenants()

    print("─" * 40)
    print("Database seeding complete.")


if __name__ == "__main__":
    asyncio.run(run_all_seeds())
