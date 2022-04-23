# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import numpy as np
import scipy.stats as st

from examples.supply_chain.rl.config import OR_NUM_CONSUMER_ACTIONS, OR_MANUFACTURE_ACTIONS
from maro.rl.policy import RuleBasedPolicy

OR_STATE_OFFSET_INDEX = {
    "is_facility": 0,
    "sale_mean": 1,
    "sale_std": 2,
    "unit_storage_cost": 3,
    "order_cost": 4,
    "storage_capacity": 5,
    "storage_levels": 6,
    "consumer_in_transit_orders": 7,
    "orders_to_distribute": 8,
    "product_idx": 9,
    "vlt": 10,
    "service_level": 11,
}


def get_element(np_state: np.ndarray, key: str) -> np.ndarray:
    offsets = np_state[0][-len(OR_STATE_OFFSET_INDEX):].astype(np.uint)
    idx = OR_STATE_OFFSET_INDEX[key]
    prev_idx = int(offsets[idx - 1]) if idx > 0 else 0
    res = np_state[:, prev_idx: offsets[idx]]
    if res.shape[0] == 1:
        return np_state[:, prev_idx: offsets[idx]].squeeze(axis=0)
    else:
        return np_state[:, prev_idx: offsets[idx]].squeeze()


class DummyPolicy(RuleBasedPolicy):
    def _rule(self, states: np.ndarray) -> list:
        return [None] * states.shape[0]


class ManufacturerBaselinePolicy(RuleBasedPolicy):
    def _rule(self, states: np.ndarray) -> np.ndarray:
        available_inventory = get_element(states, "storage_levels")
        inflight_orders = get_element(states, "consumer_in_transit_orders")
        to_distribute_orders = get_element(states, "orders_to_distribute")
        booked_table = available_inventory + inflight_orders - to_distribute_orders
        most_needed_product_id = np.expand_dims(get_element(states, "product_idx"), axis=1).astype(np.int)
        if len(booked_table.shape) < 2:
            booked_table = booked_table.reshape(1, -1)
        booked = np.squeeze(np.take_along_axis(booked_table, most_needed_product_id, axis=1), axis=1)
        sale_mean, sale_std = get_element(states, "sale_mean"), get_element(states, "sale_std")
        service_level = get_element(states, "service_level")
        vlt_buffer_days = np.where(get_element(states, "vlt")*1.3 < 2.0, 2.0, get_element(states, "vlt")*1.3)
        vlt = vlt_buffer_days + get_element(states, "vlt")
        non_facility_mask = ~(get_element(states, "is_facility").astype(np.bool))
        # stop placing orders when the facilty runs out of capacity
        # capacity_mask = np.sum(booked_table, axis=1) <= get_element(states, "storage_capacity")
        rop = vlt*sale_mean + np.sqrt(vlt.astype(float)) * sale_std * st.norm.ppf(service_level.astype(float))
        # whether replenishment point is reached
        replenishment_mask = (booked <= rop)
        replenishment_amount = ((rop - booked) / (sale_mean + 1e-8)).astype(np.int32)
        replenishment_amount = np.where(replenishment_amount >= OR_MANUFACTURE_ACTIONS, OR_MANUFACTURE_ACTIONS-1, replenishment_amount)
        return replenishment_amount * (non_facility_mask & replenishment_mask)


class ConsumerBaselinePolicy(RuleBasedPolicy):
    def _rule(self, states: np.ndarray) -> np.ndarray:
        batch_size = len(states)
        res = np.random.randint(0, high=OR_NUM_CONSUMER_ACTIONS, size=batch_size)
        # consumer_source_inventory
        available_inventory = get_element(states, "storage_levels")
        inflight_orders = get_element(states, "consumer_in_transit_orders")
        to_distribute_orders = get_element(states, "orders_to_distribute")
        booked_table = available_inventory + inflight_orders - to_distribute_orders
        most_needed_product_id = np.expand_dims(get_element(states, "product_idx"), axis=1).astype(np.int)
        if booked_table.shape[0] < 2:
            booked_table = booked_table.reshape(1, -1)
        booked = np.squeeze(np.take_along_axis(booked_table, most_needed_product_id, axis=1), axis=1)
        sale_mean, sale_std = get_element(states, "sale_mean"), get_element(states, "sale_std")
        service_level = get_element(states, "service_level")
        vlt_buffer_days = 7
        vlt = vlt_buffer_days + get_element(states, "vlt")

        non_facility_mask = ~(get_element(states, "is_facility").astype(np.bool))
        # capacity_mask = np.sum(booked_table, axis=1) <= get_element(states, "storage_capacity")
        replenishment_mask = booked <= (vlt*sale_mean + np.sqrt(vlt) * sale_std * st.norm.ppf(service_level))
        return res * (non_facility_mask & replenishment_mask)

# Q = \sqrt{2DK/h}
# Q - optimal order quantity
# D - annual demand quantity
# K - fixed cost per order, setup cost (not per unit, typically cost of ordering and shipping and handling.
#     This is not the cost of goods)
# h - annual holding cost per unit,
#     also known as carrying cost or storage cost (capital cost, warehouse space,
#     refrigeration, insurance, etc. usually not related to the unit production cost)


def _get_consumer_quantity(states: np.ndarray) -> np.ndarray:
    order_cost = get_element(states, "order_cost")
    holding_cost = get_element(states, "unit_storage_cost")
    sale_gamma = get_element(states, "sale_mean")
    consumer_quantity = np.sqrt(2 * sale_gamma * order_cost / holding_cost) / (sale_gamma + 1e-8)
    return consumer_quantity.astype(np.int32)


class ConsumerEOQPolicy(RuleBasedPolicy):
    def _rule(self, states: np.ndarray) -> np.ndarray:
        # consumer_source_inventory
        available_inventory = get_element(states, "storage_levels")
        inflight_orders = get_element(states, "consumer_in_transit_orders")
        to_distribute_orders = get_element(states, "orders_to_distribute")
        booked_table = available_inventory + inflight_orders - to_distribute_orders
        most_needed_product_id = np.expand_dims(get_element(states, "product_idx"), axis=1).astype(np.int)
        if len(booked_table.shape) < 2:
            booked_table = booked_table.reshape(1, -1)
        booked = np.squeeze(np.take_along_axis(booked_table, most_needed_product_id, axis=1), axis=1)
        sale_mean, sale_std = get_element(states, "sale_mean"), get_element(states, "sale_std")
        service_level = get_element(states, "service_level")
        vlt_buffer_days = 0 # np.where(get_element(states, "vlt")*1.3 < 2.0, 2.0, get_element(states, "vlt")*1.3)
        vlt = vlt_buffer_days + get_element(states, "vlt")
        non_facility_mask = ~(get_element(states, "is_facility").astype(np.bool))
        # stop placing orders when the facilty runs out of capacity
        # capacity_mask = np.sum(booked_table, axis=1) <= get_element(states, "storage_capacity")
        rop = vlt*sale_mean + np.sqrt(vlt.astype(float)) * sale_std * st.norm.ppf(service_level.astype(float))
        # whether replenishment point is reached
        replenishment_mask = (booked <= rop)
        replenishment_amount = ((rop - booked) / (sale_mean + 1e-8)).astype(np.int32)
        replenishment_amount = np.where(replenishment_amount >= OR_NUM_CONSUMER_ACTIONS, OR_NUM_CONSUMER_ACTIONS-1, replenishment_amount)
        return replenishment_amount * (non_facility_mask & replenishment_mask)


# parameters: (r, R), calculate according to VLT, demand variances, and service level
# replenish R - S units whenever the current stock is less than r
# S denotes the number of units in stock
class ConsumerMinMaxPolicy(RuleBasedPolicy):
    def _rule(self, states: np.ndarray) -> np.ndarray:
        # consumer_source_inventory
        available_inventory = get_element(states, "storage_levels")
        inflight_orders = get_element(states, "consumer_in_transit_orders")
        booked_table = available_inventory + inflight_orders

        # stop placing orders if no risk of out of
        most_needed_product_id = np.expand_dims(get_element(states, "product_idx"), axis=1).astype(np.int)
        booked = np.squeeze(np.take_along_axis(booked_table, most_needed_product_id, axis=1), axis=1)
        sale_mean, sale_std = get_element(states, "sale_mean"), get_element(states, "sale_std")
        service_level = get_element(states, "service_level")
        vlt_buffer_days = 10
        vlt = vlt_buffer_days + get_element(states, "vlt")
        r = vlt * sale_mean + np.sqrt(vlt) * sale_std * st.norm.ppf(service_level)

        non_facility_mask = ~(get_element(states, "is_facility").astype(np.bool))
        # stop placing orders when the facilty runs out of capacity
        capacity_mask = np.sum(booked_table, axis=1) <= get_element(states, "storage_capacity")
        sales_mask = booked <= r
        R = 3 * r
        consumer_action = (R - r) / (sale_mean + 1e-8)
        return consumer_action.astype(np.int32) * (non_facility_mask & capacity_mask & sales_mask)
