# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

import numpy as np

from maro.event_buffer import CascadeEvent
from maro.rl.policy import AbsPolicy, RLPolicy
from maro.rl.rollout import AbsAgentWrapper, AbsEnvSampler, CacheElement, SimpleAgentWrapper
from maro.simulator import Env
from maro.simulator.scenarios.supply_chain import (
    ConsumerAction, ConsumerUnit, ManufactureAction, ManufactureUnit, ProductUnit
)
from maro.simulator.scenarios.supply_chain.actions import SupplyChainAction
from maro.simulator.scenarios.supply_chain.business_engine import SupplyChainBusinessEngine
from maro.simulator.scenarios.supply_chain.facilities import FacilityInfo
from maro.simulator.scenarios.supply_chain.objects import SkuInfo, SkuMeta, SupplyChainEntity, VendorLeadingTimeInfo

from examples.supply_chain.common.balance_calculator import BalanceSheetCalculator
from examples.supply_chain.rl.algorithms.rule_based import ConsumerEOQPolicy as ConsumerBaselinePolicy

from .agent_state import serialize_state, SCAgentStates
from .config import (
    distribution_features, env_conf, seller_features, workflow_settings, ALGO
)
from .policies import agent2policy, trainable_policies

from .render_tools import SimulationTracker


class SCEnvSampler(AbsEnvSampler):
    def __init__(
        self,
        get_env: Callable[[], Env],
        policy_creator: Dict[str, Callable[[str], AbsPolicy]],
        agent2policy: Dict[Any, str],  # {agent_name: policy_name}
        trainable_policies: List[str] = None,
        agent_wrapper_cls: Type[AbsAgentWrapper] = SimpleAgentWrapper,
        reward_eval_delay: int = 0,
        get_test_env: Callable[[], Env] = None,
    ) -> None:
        super().__init__(
            get_env,
            policy_creator,
            agent2policy,
            trainable_policies=trainable_policies,
            agent_wrapper_cls=agent_wrapper_cls,
            reward_eval_delay=reward_eval_delay,
            get_test_env=get_test_env,
        )
        self.baseline_policy = ConsumerBaselinePolicy('baseline_eoq')

        self._env_settings: dict = workflow_settings

        self._balance_calculator: BalanceSheetCalculator = BalanceSheetCalculator(self._learn_env, TEAM_REWARD)

        self._configs: dict = self._learn_env.configs

        self._entity_dict: Dict[int, SupplyChainEntity] = {
            entity.id: entity
            for entity in self._learn_env.business_engine.get_entity_list()
        }

        self._summary: dict = self._learn_env.summary['node_mapping']

        # Key: Unit id; Value: (unit.data_model_name, unit.data_model_index, unit.facility.id, SkuInfo)
        self._units_mapping: Dict[int, Tuple[str, int, int, SkuInfo]] = self._summary["unit_mapping"]

        self._sku_metas: Dict[int, SkuMeta] = self._summary["skus"]
        self._global_sku_id2idx: Dict[int, int] = {
            sku_id: idx
            for idx, sku_id in enumerate(self._sku_metas.keys())
        }
        self._sku_number: int = len(self._sku_metas)

        self._facility_info_dict: Dict[int, FacilityInfo] = self._summary["facilities"]

        self._unit2product_unit: Dict[int, int] = self._get_unit2product_unit(self._facility_info_dict)

        # Key 1: Facility id; Key 2: Product id; Value: Index in product list
        self._product_id2idx: Dict[int, Dict[int, int]] = self._get_product_id2idx(self._facility_info_dict)

        # Key: Consumer unit id; Value: corresponding product id.
        self._consumer2product_id: Dict[int, int] = self._get_consumer2product_id(self._facility_info_dict)

        self._cur_metrics: dict = self._learn_env.metrics

        # Key: facility/unit id; Value: (balance, reward)
        self._cur_balance_sheet_reward: Dict[int, Tuple[float, float]] = {}

        # States of current tick, extracted from snapshot list.
        self._cur_distribution_states: Optional[np.ndarray] = None
        self._cur_consumer_states: Optional[np.ndarray] = None
        self._cur_seller_states: Optional[np.ndarray] = None

        # Key: facility id; List Index: sku idx; Value: in transition product quantity.
        self._facility_in_transit_orders: Dict[int, List[int]] = {}
        self._facility_to_distribute_orders: Dict[int, List[int]] = {}

        self._facility_product_utilization: Dict[int, int] = {}

        # Key: facility id
        self._storage_product_quantity: Dict[int, List[int]] = defaultdict(lambda: [0] * self._sku_number)

        self._agent_states: SCAgentStates = SCAgentStates(
            entity_dict=self._entity_dict,
            facility_info_dict=self._facility_info_dict,
            global_sku_id2idx=self._global_sku_id2idx,
            sku_number=self._sku_number,
            max_src_per_facility=self._summary["max_sources_per_facility"],
            max_price=self._summary["max_price"],
            settings=self._env_settings,
        )
        self._stock_status = {}
        self._demand_status = {}
        # key: product unit id, value: number
        # self._orders_from_downstreams = {}
        # self._consumer_orders = {}
        self._order_in_transit_status = {}
        self._order_to_distribute_status = {}
        self._sold_status = {}
        self._reward_status = {}
        self._balance_status = {}

    def _get_unit2product_unit(self, facility_info_dict: Dict[int, FacilityInfo]) -> Dict[int, int]:
        unit2product: Dict[int, int] = {}
        for facility_info in facility_info_dict.values():
            for product_info in facility_info.products_info.values():
                for unit in (
                    product_info, product_info.seller_info, product_info.consumer_info, product_info.manufacture_info
                ):
                    if unit is not None:
                        unit2product[unit.id] = product_info.id
        return unit2product

    def _get_product_id2idx(self, facility_info_dict: Dict[int, FacilityInfo]) -> Dict[int, Dict[int, int]]:
        # Key 1: facility id; Key 2: product id; Value: index in product list.
        product_id2idx: Dict[int, Dict[int, int]] = defaultdict(dict)

        for facility_id, facility_info in facility_info_dict.items():
            if facility_info.storage_info is not None:
                for i, pid in enumerate(facility_info.storage_info.product_list):
                    product_id2idx[facility_id][pid] = i

        return product_id2idx

    def _get_consumer2product_id(self, facility_info_dict: Dict[int, FacilityInfo]) -> Dict[int, int]:
        consumer2product_id: Dict[int, int] = {}

        for facility_info in facility_info_dict.values():
            for product_id, product in facility_info.products_info.items():
                if product.consumer_info:
                    consumer2product_id[product.consumer_info.id] = product_id

        return consumer2product_id

    def _get_reward_for_entity(self, entity: SupplyChainEntity, bwt: Tuple[float, float]) -> float:
        if entity.class_type == ConsumerUnit:
            return np.float32(bwt[1]) / np.float32(self._env_settings["reward_normalization"])
        else:
            return .0

    def get_or_policy_state(self, entity: SupplyChainEntity) -> np.ndarray:
        # TODO: check the correctness of the implementation
        if entity.skus is None:
            return np.array([1])

        np_state, offsets = [0], [1]

        def extend_state(value: list) -> None:
            np_state.extend(value)
            offsets.append(len(np_state))

        product_unit_id = entity.id if issubclass(entity.class_type, ProductUnit) else entity.parent_id

        product_index = self._balance_calculator.product_id2idx.get(product_unit_id, None)
        unit_storage_cost = self._balance_calculator.products[product_index][4] if product_index is not None else 0

        product_metrics = self._cur_metrics["products"].get(product_unit_id, None)
        extend_state([product_metrics["sale_mean"] if product_metrics else 0])
        extend_state([product_metrics["sale_std"] if product_metrics else 0])

        extend_state([unit_storage_cost])
        extend_state([1])
        facility_info = self._facility_info_dict[entity.facility_id]
        product_info = facility_info.products_info[entity.skus.id]
        if product_info.consumer_info is not None:
            idx = product_info.consumer_info.node_index
            np_state[-1] = self._learn_env.snapshot_list["consumer"][
                self._learn_env.tick:idx:"order_cost"
            ].flatten()[0]

        be = self._env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)
        extend_state([facility_info.storage_info.config[0].capacity])
        extend_state(self._storage_product_quantity[entity.facility_id])
        extend_state(self._facility_in_transit_orders[entity.facility_id])
        extend_state(self._facility_to_distribute_orders[entity.facility_id])

        # extend_state([self._product_id2idx[entity.facility_id][entity.skus.id] + 1])  # TODO: check +1 or not
        extend_state([self._global_sku_id2idx[entity.skus.id]])
        extend_state([be.world.get_facility_by_id(entity.facility_id).get_max_vlt(entity.skus.id)])
        extend_state([entity.skus.service_level])
        return np.array(np_state + offsets)

    def get_rl_policy_state(self, entity_id: int, entity: SupplyChainEntity) -> np.ndarray:
        state = self._agent_states.update_entity_state(
            entity_id=entity_id,
            tick=self._learn_env.tick,
            cur_metrics=self._cur_metrics,
            cur_distribution_states=self._cur_distribution_states,
            cur_seller_states=self._cur_seller_states,
            cur_consumer_states=self._cur_consumer_states,
            accumulated_balance=self._balance_calculator.accumulated_balance_sheet[entity_id],
            storage_product_quantity=self._storage_product_quantity,
            facility_product_utilization=self._facility_product_utilization,
            facility_in_transit_orders=self._facility_in_transit_orders,
        )
        
        baseline_action = 0
        if issubclass(entity.class_type, ConsumerUnit):
            bs_state = self.get_or_policy_state(entity)
            bs_state = np.reshape(bs_state, (1, -1))
            baseline_action = self.baseline_policy.get_actions(bs_state)[0]
        state['baseline_action'] = baseline_action

        self._stock_status[entity.id] = state['inventory_in_stock']

        facility = self._facility_info_dict[entity.facility_id]
        if entity.skus and (facility.products_info[entity.skus.id].seller_info is not None):        
            self._demand_status[entity.id] = state['demand_hist'][-1]
            self._sold_status[entity.id] = state['sale_hist'][-1]
        else:
            self._demand_status[entity.id] = state['sale_mean']
            self._sold_status[entity.id] = state['sale_mean']

        self._order_in_transit_status[entity.id] = state['inventory_in_transit']
        self._order_to_distribute_status[entity.id] = state['distributor_in_transit_orders_qty']

        np_state = serialize_state(state)
        return np_state

    def _get_entity_state(self, entity_id: int) -> np.ndarray:
        entity = self._entity_dict[entity_id]

        if isinstance(self._policy_dict[self._agent2policy[entity_id]], RLPolicy):
            return self.get_rl_policy_state(entity_id, entity)
        else:
            return self.get_or_policy_state(entity)

    def _get_global_and_agent_state(self, event: CascadeEvent, tick: int = None) -> tuple:
        """Update the status variables first, then call the state shaper for each agent."""
        if tick is None:
            tick = self._learn_env.tick
        else:
            # To make sure the usage of metrics is correct, the tick should be same to the current env tick.
            assert tick == self._learn_env.tick

        self._cur_metrics = self._learn_env.metrics

        # Get balance info of current tick from balance calculator.
        self._cur_balance_sheet_reward = self._balance_calculator.calc_and_update_balance_sheet(tick=tick)

        # Get distribution features of current tick from snapshot list.
        self._cur_distribution_states = self._learn_env.snapshot_list["distribution"][
            tick::distribution_features
        ].flatten().reshape(-1, len(distribution_features)).astype(np.int)

        # Get consumer features of specific ticks from snapshot list.
        consumption_hist_ticks = [tick - i for i in range(self._env_settings['consumption_hist_len'] - 1, -1, -1)]
        self._cur_consumer_states = self._learn_env.snapshot_list["consumer"][
            consumption_hist_ticks::"latest_consumptions"
        ].flatten().reshape(-1, len(self._learn_env.snapshot_list["consumer"]))

        # Get seller features of specific ticks from snapshot list.
        sale_hist_ticks = [tick - i for i in range(self._env_settings['sale_hist_len'] - 1, -1, -1)]
        self._cur_seller_states = self._learn_env.snapshot_list["seller"][
            sale_hist_ticks::seller_features
        ].astype(np.int)

        # 1. Update storage product quantity info.
        # 2. Update facility product utilization info.
        # 3. Update facility in transition order quantity info.
        for facility_id, facility_info in self._facility_info_dict.items():
            # Reset for each step
            self._facility_product_utilization[facility_id] = 0
            self._facility_in_transit_orders[facility_id] = [0] * self._sku_number
            self._facility_to_distribute_orders[facility_id] = [0] * self._sku_number

            product_quantities = self._learn_env.snapshot_list["storage"][
                tick:facility_info.storage_info.node_index:"product_quantity"
            ].flatten().astype(np.int)

            for pid, index in self._product_id2idx[facility_id].items():
                product_quantity = product_quantities[index]

                self._storage_product_quantity[facility_id][self._global_sku_id2idx[pid]] = product_quantity
                self._facility_product_utilization[facility_id] += product_quantity

            for sku_id, quantity in self._cur_metrics['facilities'][facility_id]["in_transit_orders"].items():
                self._facility_in_transit_orders[facility_id][self._global_sku_id2idx[sku_id]] = quantity

            for sku_id, quantity in self._cur_metrics['facilities'][facility_id]["pending_order"].items():
                self._facility_to_distribute_orders[facility_id][self._global_sku_id2idx[sku_id]] = quantity

        # to keep track infor
        for id_, entity in self._entity_dict.items():
            self.get_rl_policy_state(id_, entity)

        state = {
            id_: self._get_entity_state(id_)
            for id_ in self._agent2policy.keys()
        }
        return None, state

    def _get_reward(self, env_action_dict: Dict[Any, object], event: object, tick: int) -> Dict[Any, float]:
        # get related product, seller, consumer, manufacture unit id
        # NOTE: this mapping does not contain facility id, so if id is not exist, then means it is a facility
        self._cur_balance_sheet_reward = self._balance_calculator.calc_and_update_balance_sheet(tick=tick)
        self._reward_status = {f_id: np.float32(reward[1]) for f_id, reward in self._cur_balance_sheet_reward.items()}
        self._balance_status = {f_id: np.float32(reward[0]) for f_id, reward in self._cur_balance_sheet_reward.items()}
        return {
            unit_id: self._get_reward_for_entity(self._entity_dict[unit_id], bwt)
            for unit_id, bwt in self._cur_balance_sheet_reward.items()
            if unit_id in self._agent2policy
        }

    def _translate_to_env_action(self, action_dict: Dict[Any, np.ndarray], event: object) -> Dict[Any, object]:
        env_action_dict: Dict[int, SupplyChainAction] = {}

        for agent_id, action in action_dict.items():
            entity_id = agent_id
            env_action: Optional[SupplyChainAction] = None
            if np.isscalar(action):
                action = [action]
            product_unit_id: int = self._unit2product_unit[entity_id]
            # Consumer action
            if issubclass(self._entity_dict[agent_id].class_type, ConsumerUnit):
                product_id: int = self._consumer2product_id.get(entity_id, 0)

                # TODO: vehicle type selection and source selection
                facility_info: FacilityInfo = self._facility_info_dict[self._entity_dict[entity_id].facility_id]
                vlt_info_cadidates: List[VendorLeadingTimeInfo] = [
                    vlt_info
                    for vlt_info in facility_info.upstream_vlt_infos[product_id]
                    if any([
                        self._env_settings["default_vehicle_type"] == None,
                        vlt_info.vehicle_type == self._env_settings["default_vehicle_type"],
                    ])
                ]

                if len(vlt_info_cadidates):
                    src_f_id = vlt_info_cadidates[0].src_facility.id
                    vehicle_type = vlt_info_cadidates[0].vehicle_type
                    
                    if (ALGO == "PPO" and isinstance(self._policy_dict[self._agent2policy[agent_id]], RLPolicy)):
                        or_action = self._agent_state_dict[agent_id][-1]
                        action_idx = max(0, int(action[0] - 1 + or_action))
                    else:
                        action_idx = action[0]
                    action_quantity = int(int(action_idx) * self._cur_metrics["products"][product_unit_id]["sale_mean"])

                    # Ignore 0 quantity to reduce action number
                    if action_quantity:
                        env_action = ConsumerAction(entity_id, product_id, src_f_id, action_quantity, vehicle_type)

            # Manufacture action
            elif issubclass(self._entity_dict[agent_id].class_type, ManufactureUnit):
                # sku = self._units_mapping[entity_id][3]
                # if sku.production_rate:
                #     env_action = ManufactureAction(id=entity_id, production_rate=int(sku.production_rate))
                action_quantity = int(action[0] * self._cur_metrics["products"][product_unit_id]["sale_mean"])
                env_action = ManufactureAction(id=entity_id, production_rate=action_quantity)
            if env_action:
                env_action_dict[agent_id] = env_action

        return env_action_dict

    def _post_step(self, cache_element: CacheElement, reward: Dict[Any, float]) -> None:
        tick = cache_element.tick
        total_sold = self._learn_env.snapshot_list["seller"][tick::"total_sold"].reshape(-1)
        total_demand = self._learn_env.snapshot_list["seller"][tick::"total_demand"].reshape(-1)
        self._info["sold"] = total_sold
        self._info["demand"] = total_demand
        self._info["sold/demand"] = self._info["sold"] / self._info["demand"]

    def _post_eval_step(self, cache_element: CacheElement, reward: Dict[Any, float]) -> None:
        self._post_step(cache_element, reward)

    def reset(self):
        # super().reset()
        self._balance_calculator.reset()
        self.total_balance = 0.0

    def eval(self, policy_state: Dict[str, object] = None) -> dict:
        tracker = SimulationTracker(env_conf["durations"], 1, self, [0, env_conf["durations"]])
        step_idx = 0
        self._env = self._test_env
        if policy_state is not None:
            self.set_policy_state(policy_state)

        self._agent_wrapper.exploit()
        self.reset()
        self._env.reset()
        is_done = False
        _, self._event, _ = self._env.step(None)
        self._state, self._agent_state_dict = self._get_global_and_agent_state(self._event)
        while not is_done:
            action_dict = self._agent_wrapper.choose_actions(self._agent_state_dict)
            agent_state_dict={id_: state for id_, state in self._agent_state_dict.items() if id_ in self._trainable_agents}
            env_action_dict = self._translate_to_env_action(action_dict, self._event)
            
            # Store experiences in the cache
            cache_element = CacheElement(
                    tick=self._env.tick,
                    event=self._event,
                    state=self._state,
                    agent_state_dict=agent_state_dict,
                    action_dict={id_: action for id_, action in action_dict.items() if id_ in self._trainable_agents},
                    env_action_dict={
                        id_: env_action for id_, env_action in env_action_dict.items() if id_ in self._trainable_agents
                    },
                )
            self._trans_cache.append(cache_element)
            # Update env and get new states (global & agent)
            _, self._event, is_done = self._env.step(list(env_action_dict.values()))
            reward = self._get_reward(cache_element.env_action_dict, cache_element.event, cache_element.tick)
            consumer_action_dict = {}
            for entity_id, entity in self._entity_dict.items():
                if issubclass(entity.class_type, ConsumerUnit):
                    action = (action_dict[entity_id] if np.isscalar(action_dict[entity_id]) else action_dict[entity_id][0])
                    consumer_action_dict[entity_id] = (action, reward[entity_id])
            print(step_idx, consumer_action_dict)
            self._state, self._agent_state_dict = (None, {}) if is_done \
                else self._get_global_and_agent_state(self._event)

            step_balances = self._balance_status
            step_rewards = self._reward_status

            tracker.add_sample(0, step_idx, sum(step_balances.values()), sum(
                step_rewards.values()), step_balances, step_rewards)
            stock_status = self._stock_status
            order_in_transit_status = self._order_in_transit_status
            demand_status = self._demand_status
            sold_status = self._sold_status
            reward_status = self._reward_status

            balance_status = self._balance_status
            order_to_distribute_status = self._order_to_distribute_status

            tracker.add_sku_status(0, step_idx, stock_status,
                                   order_in_transit_status, demand_status, sold_status,
                                   reward_status, balance_status,
                                   order_to_distribute_status)
            step_idx += 1
            self._info["sold"] = 0
            self._info["demand"] = 1
            self._info["sold/demand"] = self._info["sold"] / self._info["demand"]
        return {"info": [self._info], "tracker": tracker}


def env_sampler_creator(policy_creator) -> SCEnvSampler:
    return SCEnvSampler(
        get_env=lambda: Env(**env_conf),
        policy_creator=policy_creator,
        agent2policy=agent2policy,
        trainable_policies=trainable_policies,
    )
