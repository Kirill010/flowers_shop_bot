from aiogram import Router

from . import admin, cart, catalog, checkout, fallback, gifts, info, manager, menu, my_orders, payments, reviews


def all_routers() -> list[Router]:
    """Порядок важен: сначала более точные разделы, в самом конце — «не понял»."""
    return [menu.router, admin.router, catalog.router, cart.router, checkout.router, info.router,
            manager.router, gifts.router, payments.router, reviews.router, my_orders.router, fallback.router]
