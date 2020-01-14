import os
import time
import logging
import glob
import pickle
import gym
import ray

from copy import deepcopy as dcopy

import numpy as np

from .envs import *
from .models import *
from .utils import *
from .constants import *
from .arguments import *

logger = logging.getLogger(__name__)


def policy_mapping_fn_i2i(agent_id):
    """A policy_mapping_fn that maps agent i to policy i.
    """
    return policy_i2id(agent_id2i(agent_id))


def is_arena_env(each_env):
    """Check if a env (string) is an arena env.
    """
    return each_env[:len(ARENA_ENV_PREFIX)] == ARENA_ENV_PREFIX


def is_all_arena_env(env):
    """Check if all env in a grid_search env are arena env.
    If env is not a grid_search, return is_arena_env.
    """
    if is_grid_search(env):
        for each_env in env["grid_search"]:
            if not is_arena_env(each_env):
                return False
        return True
    else:
        return is_arena_env(env)


def is_any_arena_env(env):
    """Check if any env in a grid_search env is arena env.
    If env is not a grid_search, return is_arena_env.
    """
    if is_grid_search(env):
        for each_env in env["grid_search"]:
            if is_arena_env(each_env):
                return True
        return False
    else:
        return is_arena_env(env)


def get_checkpoint_path(logdir, population_i, iteration_i):
    """Get checkpoint_path from population_i and iteration_i.
    """

    checkpoint_path = os.path.join(
        logdir,
        "{}{}{}-{}{}".format(
            CHECKPOINT_PATH_PREFIX,
            CHECKPOINT_PATH_POPULATION_PREFIX,
            population_i,
            CHECKPOINT_PATH_ITERATION_PREFIX,
            iteration_i,
        )
    )

    return checkpoint_path


def get_possible_populations(logdir):
    """Get possible populations on the disk, sorted in order
    """

    possible_populations = []
    checkpoint_path_search_prefix = os.path.join(
        logdir,
        "{}{}".format(
            CHECKPOINT_PATH_PREFIX,
            CHECKPOINT_PATH_POPULATION_PREFIX,
        )
    )
    for file in glob.glob(checkpoint_path_search_prefix + "*"):
        population_i = int(
            file.split(
                checkpoint_path_search_prefix
            )[1].split("-")[0]
        )
        if population_i not in possible_populations:
            possible_populations += [
                population_i
            ]
    possible_populations = np.asarray(
        possible_populations
    )
    possible_populations.sort()
    return possible_populations


def get_possible_iterations(logdir, population_i):
    """Get possible iterations on the disk, sorted in order
    """

    possible_iterations = []
    checkpoint_path_search_prefix = os.path.join(
        logdir,
        "{}{}{}-{}".format(
            CHECKPOINT_PATH_PREFIX,
            CHECKPOINT_PATH_POPULATION_PREFIX,
            population_i,
            CHECKPOINT_PATH_ITERATION_PREFIX,
        )
    )
    for file in glob.glob(checkpoint_path_search_prefix + "*"):
        possible_iterations += [
            int(
                file.split(
                    checkpoint_path_search_prefix
                )[1].split("-")[0]
            )
        ]
    possible_iterations = np.asarray(
        possible_iterations
    )
    possible_iterations.sort()
    return possible_iterations


def on_train_result(info):
    """Function called after each trained iteration
    """

    if info["result"]["training_iteration"] % info["trainer"].config["iterations_per_reload"] == 0:

        logger.info(
            "Save learning policy and reload all policies."
        )

        # save learning policies
        for policy_id in info["trainer"].config["learning_policy_ids"]:

            policy = info["trainer"].get_policy(
                policy_id
            )

            # check if policy has population_i assigned
            if hasattr(policy, "population_i"):
                # if so, get it
                population_i = policy.population_i
            else:
                possible_populations = get_possible_populations(
                    logdir=info["trainer"].logdir,
                )
                # if not, generate one from those have not been used
                population_i = np.random.choice(
                    list_subtract(
                        range(
                            info["trainer"].config["size_population"]
                        ),
                        possible_populations
                    )
                )

            iteration_i = info["trainer"].iteration

            # get checkpoint_path by population_i and recent iteration
            checkpoint_path = get_checkpoint_path(
                logdir=info["trainer"].logdir,
                population_i=population_i,
                iteration_i=iteration_i,
            )

            prepare_path(checkpoint_path)

            save_message = "Save learning policy {} in population {} at iteration {}".format(
                policy_id,
                population_i,
                iteration_i,
            )

            # save to checkpoint_path
            try:
                pickle.dump(
                    info["trainer"].get_policy(
                        policy_id
                    ).get_weights(),
                    open(
                        checkpoint_path,
                        "wb"
                    )
                )
                logger.info("{} succeed".format(
                    save_message,
                ))
            except Exception as e:
                logger.warning("{} failed: {}.".format(
                    save_message,
                    e,
                ))

        possible_populations = get_possible_populations(
            logdir=info["trainer"].logdir,
        )

        # reload all policies
        for policy_id in (info["trainer"].config["learning_policy_ids"] + info["trainer"].config["playing_policy_ids"]):

            policy = info["trainer"].get_policy(
                policy_id
            )

            population_i = np.random.choice(
                range(info["trainer"].config["size_population"]),
            )

            load_message = "Load {} policy {} in population {}".format(
                {
                    True: "learning",
                    False: "playing",
                }[
                    policy_id in info["trainer"].config["learning_policy_ids"]
                ],
                policy_id,
                population_i,
            )

            if population_i in possible_populations:

                # get possible_iterations
                possible_iterations = get_possible_iterations(
                    logdir=info["trainer"].logdir,
                    population_i=population_i,
                )

                if policy_id in info["trainer"].config["learning_policy_ids"]:
                    # for learning policy, it only reload to the recent checkpoint
                    load_recent_prob = 1.0
                elif policy_id in info["trainer"].config["playing_policy_ids"]:
                    # for playing policy, it reload according to playing_policy_load_recent_prob
                    load_recent_prob = info["trainer"].config["playing_policy_load_recent_prob"]
                else:
                    raise NotImplementedError

                principle = str(
                    np.random.choice(
                        ["recent", "uniform"],
                        replace=False,
                        p=[
                            load_recent_prob,
                            1.0 - load_recent_prob
                        ]
                    )
                )

                if principle in ["recent"]:
                    iteration_i = possible_iterations[-1]
                elif principle in ["uniform"]:
                    iteration_i = np.random.choice(
                        possible_iterations,
                        replace=False,
                    )
                else:
                    raise NotImplementedError

                # get checkpoint_path, population_i is re-generated, iteration_i is specified
                checkpoint_path = get_checkpoint_path(
                    logdir=info["trainer"].logdir,
                    population_i=population_i,
                    iteration_i=iteration_i,
                )

                load_message = "{} at iteration {}".format(
                    load_message,
                    iteration_i,
                )

                try:
                    policy.set_weights(
                        pickle.load(
                            open(
                                checkpoint_path,
                                "rb"
                            )
                        )
                    )
                    policy.population_i = population_i
                    logger.info("{} succeed. A result of load_recent_prob={} with principle={}".format(
                        load_message,
                        load_recent_prob,
                        principle,
                    ))
                except Exception as e:
                    logger.warning("{} failed: {}.".format(
                        load_message,
                        e,
                    ))

            else:
                # if population_i is not in possible_populations yet
                logger.info("{} skipped, the population_i does not exist yet, creating branch (possible_populations are {})".format(
                    load_message,
                    possible_populations,
                ))
                policy.population_i = population_i


def create_arena_exps(exps, args, parser):
    """Create arena_exps from exps
    Expand exps with grid_search, this is implemented to override the default support of grid_search for customized configs
    """
    arena_exps = {}

    for exp_key in exps.keys():

        if args.dummy:
            logger.info(
                "Run in dummy mode. "
                "Overriding configs."
            )
            exps[exp_key]["config"]["num_gpus"] = 0
            exps[exp_key]["config"]["num_workers"] = 1
            exps[exp_key]["config"]["num_envs_per_worker"] = 1
            exps[exp_key]["config"]["sample_batch_size"] = 100
            exps[exp_key]["config"]["train_batch_size"] = 100
            exps[exp_key]["config"]["sgd_minibatch_size"] = 100

        env_keys = dcopy(exps[exp_key]["env"])
        env_keys = get_list_from_gridsearch(env_keys)

        for env in env_keys:

            sensors_keys = dcopy(
                exps[exp_key]["config"]["env_config"]["sensors"])
            sensors_keys = get_list_from_gridsearch(sensors_keys)

            if not is_arena_env(env):
                if is_gridsearch_match(sensors_keys, []):
                    logger.warning(
                        "None-arena env does not support the config of sensors. "
                        "Overriding it to none."
                    )
                    sensors_keys = []

            for sensors in sensors_keys:

                actor_critic_obs_keys = dcopy(
                    exps[exp_key]["config"]["actor_critic_obs"])
                actor_critic_obs_keys = get_list_from_gridsearch(
                    actor_critic_obs_keys)

                if not is_arena_env(env):
                    if is_gridsearch_match(actor_critic_obs_keys, []):
                        logger.warning(
                            "None-arena env does not support the config of actor_critic_obs. "
                            "Overriding it to none."
                        )
                        actor_critic_obs_keys = []
                else:
                    # check config valid
                    for actor_critic_obs in actor_critic_obs_keys:
                        if not((len(actor_critic_obs) == 0) or (len(actor_critic_obs) == 2)):
                            raise Exception(
                                "actor_critic_obs can only be [] or [xx, yy]")

                for actor_critic_obs in actor_critic_obs_keys:

                    multi_agent_obs_keys = dcopy(
                        exps[exp_key]["config"]["env_config"]["multi_agent_obs"])
                    multi_agent_obs_keys = get_list_from_gridsearch(
                        multi_agent_obs_keys)

                    if not is_arena_env(env):
                        if is_gridsearch_match(multi_agent_obs_keys, []):
                            logger.warning(
                                "None-arena env does not support the config of multi_agent_obs. "
                                "Overriding it to none."
                            )
                            multi_agent_obs_keys = []

                    for multi_agent_obs in multi_agent_obs_keys:

                        # If xx or yy are not in sensors, the config of sensors will be overrided to include xx and yy.
                        for each_actor_critic_obs in actor_critic_obs:
                            if each_actor_critic_obs not in multi_agent_obs:
                                multi_agent_obs += [each_actor_critic_obs]

                        if is_arena_env(env):

                            # create dummy_env to get parameters/setting of env
                            env_config = dcopy(
                                exps[exp_key]["config"]["env_config"]
                            )
                            env_config["sensors"] = sensors
                            env_config["multi_agent_obs"] = multi_agent_obs
                            dummy_env = ArenaRllibEnv(
                                env=env,
                                env_config=env_config,
                            )

                            number_agents = dcopy(
                                dummy_env.number_agents
                            )
                            obs_space = dcopy(
                                dummy_env.observation_space
                            )
                            action_space = dcopy(
                                dummy_env.action_space
                            )

                            dummy_env.close()

                        else:

                            number_agents = "none"
                            obs_space = "none"
                            action_space = "none"

                        num_learning_policies_keys = dcopy(
                            exps[exp_key]["config"]["num_learning_policies"])
                        num_learning_policies_keys = get_list_from_gridsearch(
                            num_learning_policies_keys)
                        if not is_arena_env(env):
                            if is_gridsearch_match(num_learning_policies_keys, "none"):
                                logger.warning(
                                    "None-arena env does not support the config of num_learning_policies. "
                                    "Overriding it to none."
                                )
                                num_learning_policies_keys = ["none"]

                        for num_learning_policies in num_learning_policies_keys:

                            # get other keys
                            num_learning_policies_other_keys = dcopy(
                                num_learning_policies_keys)
                            num_learning_policies_other_keys.remove(
                                num_learning_policies)

                            # override config
                            if num_learning_policies == "all":
                                num_learning_policies = dcopy(number_agents)

                            # if in other keys ofter override, skip this config
                            if num_learning_policies in num_learning_policies_other_keys:
                                continue

                            share_layer_policies_keys = dcopy(
                                exps[exp_key]["config"]["share_layer_policies"])
                            share_layer_policies_keys = get_list_from_gridsearch(
                                share_layer_policies_keys)
                            if not is_arena_env(env):
                                if is_gridsearch_match(share_layer_policies_keys, "none"):
                                    logger.warning(
                                        "None-arena env does not support the config of share_layer_policies. "
                                        "Overriding it to none."
                                    )
                                    share_layer_policies_keys = ["none"]

                            for share_layer_policies in share_layer_policies_keys:

                                # get other keys
                                share_layer_policies_other_keys = dcopy(
                                    share_layer_policies_keys)
                                share_layer_policies_other_keys.remove(
                                    share_layer_policies)

                                is_shuffle_agents = dcopy(
                                    exps[exp_key]["config"]["env_config"]["is_shuffle_agents"]
                                )

                                # override config
                                if isinstance(share_layer_policies, str):

                                    if share_layer_policies == "team":

                                        if is_shuffle_agents:
                                            logger.warning(
                                                "If share_layer_policies==none, config.env_config.is_shuffle_agents needs to be False. Overriding config.env_config.is_shuffle_agents to False."
                                            )
                                            is_shuffle_agents = False

                                        share_layer_policies = dcopy(
                                            get_social_config(
                                                env
                                            )
                                        )

                                # if in other keys ofter override, skip this config
                                if share_layer_policies in share_layer_policies_other_keys:
                                    continue

                                arena_exp_key = "{},e={},ot={},mao={},nlp={},slp={},aco={}".format(
                                    exp_key,
                                    env,
                                    sensors,
                                    multi_agent_obs,
                                    num_learning_policies,
                                    share_layer_policies,
                                    actor_critic_obs,
                                )

                                arena_exps[arena_exp_key] = dcopy(
                                    exps[exp_key]
                                )

                                arena_exps[arena_exp_key]["config"]["env"] = dcopy(
                                    env
                                )
                                arena_exps[arena_exp_key]["config"]["env_config"]["sensors"] = dcopy(
                                    sensors
                                )
                                arena_exps[arena_exp_key]["config"]["env_config"]["multi_agent_obs"] = dcopy(
                                    multi_agent_obs
                                )
                                arena_exps[arena_exp_key]["config"]["number_agents"] = dcopy(
                                    number_agents
                                )
                                arena_exps[arena_exp_key]["config"]["obs_space"] = dcopy(
                                    obs_space
                                )
                                arena_exps[arena_exp_key]["config"]["act_space"] = dcopy(
                                    action_space
                                )
                                arena_exps[arena_exp_key]["config"]["num_learning_policies"] = dcopy(
                                    num_learning_policies
                                )
                                arena_exps[arena_exp_key]["config"]["env_config"]["is_shuffle_agents"] = dcopy(
                                    is_shuffle_agents
                                )
                                arena_exps[arena_exp_key]["config"]["share_layer_policies"] = dcopy(
                                    share_layer_policies
                                )
                                arena_exps[arena_exp_key]["config"]["actor_critic_obs"] = dcopy(
                                    actor_critic_obs
                                )

                                if args.eager:
                                    arena_exps[arena_exp_key]["config"]["eager"] = True

                                if is_arena_env(env):

                                    # policies: config of policies
                                    arena_exps[arena_exp_key]["config"]["multiagent"] = {
                                    }
                                    arena_exps[arena_exp_key]["config"]["multiagent"]["policies"] = {
                                    }
                                    # learning_policy_ids: a list of policy ids of which the policy is trained
                                    arena_exps[arena_exp_key]["config"]["learning_policy_ids"] = [
                                    ]
                                    # playing_policy_ids: a list of policy ids of which the policy is not trained
                                    arena_exps[arena_exp_key]["config"]["playing_policy_ids"] = [
                                    ]

                                    # create configs of learning policies
                                    for learning_policy_i in range(arena_exps[arena_exp_key]["config"]["num_learning_policies"]):
                                        learning_policy_id = policy_i2id(
                                            learning_policy_i
                                        )
                                        arena_exps[arena_exp_key]["config"]["learning_policy_ids"] += [
                                            learning_policy_id
                                        ]

                                    if arena_exps[arena_exp_key]["run"] not in ["PPO"]:
                                        # build custom_action_dist to be playing mode dist (no exploration)
                                        # TODO: support pytorch policy and other algorithms, currently only add support for tf_action_dist on PPO
                                        # see this issue for a fix: https://github.com/ray-project/ray/issues/5729
                                        raise NotImplementedError

                                    # create configs of playing policies
                                    for playing_policy_i in range(arena_exps[arena_exp_key]["config"]["num_learning_policies"], arena_exps[arena_exp_key]["config"]["number_agents"]):
                                        playing_policy_id = policy_i2id(
                                            playing_policy_i
                                        )
                                        arena_exps[arena_exp_key]["config"]["playing_policy_ids"] += [
                                            playing_policy_id
                                        ]

                                    if len(arena_exps[arena_exp_key]["config"]["playing_policy_ids"]) > 0:

                                        if arena_exps[arena_exp_key]["config"]["env_config"]["is_shuffle_agents"] == False:
                                            logger.warning(
                                                "There are playing policies, which keeps loading learning policies. This means you need to shuffle agents so that the learning policies can generalize to playing policies. Overriding config.env_config.is_shuffle_agents to True."
                                            )
                                            arena_exps[arena_exp_key]["config"]["env_config"]["is_shuffle_agents"] = True

                                    elif len(arena_exps[arena_exp_key]["config"]["playing_policy_ids"]) == 0:

                                        if arena_exps[arena_exp_key]["config"]["playing_policy_load_recent_prob"] != "none":
                                            logger.warning(
                                                "There are no playing agents. Thus, config.playing_policy_load_recent_prob is invalid. Overriding config.playing_policy_load_recent_prob to none."
                                            )
                                            arena_exps[arena_exp_key]["config"]["playing_policy_load_recent_prob"] = "none"

                                    else:
                                        raise ValueError

                                    # apply configs of all policies
                                    for policy_i in range(arena_exps[arena_exp_key]["config"]["number_agents"]):

                                        policy_id = policy_i2id(policy_i)

                                        policy_config = {}

                                        if policy_id in arena_exps[arena_exp_key]["config"]["playing_policy_ids"]:
                                            from ray.rllib.models.tf.tf_action_dist import Deterministic as DeterministiContinuous
                                            policy_config["custom_action_dist"] = {
                                                "Discrete": DeterministicCategorical,
                                                "Box": DeterministiContinuous
                                            }[
                                                arena_exps[arena_exp_key]["config"]["act_space"].__class__.__name__
                                            ]

                                        policy_config["model"] = {}

                                        if arena_exps[arena_exp_key]["config"]["share_layer_policies"] != "none":
                                            policy_config["model"]["custom_model"] = "ArenaPolicy"

                                        if arena_exps[arena_exp_key]["config"]["share_layer_policies"] != "none":
                                            policy_config["model"]["custom_options"] = {
                                            }
                                            policy_config["model"]["custom_options"]["shared_scope"] = dcopy(
                                                arena_exps[arena_exp_key]["config"]["share_layer_policies"][
                                                    find_in_list_of_list(
                                                        arena_exps[arena_exp_key]["config"]["share_layer_policies"], policy_i
                                                    )[0]
                                                ]
                                            )

                                        arena_exps[arena_exp_key]["config"]["multiagent"]["policies"][policy_id] = (
                                            None,
                                            arena_exps[arena_exp_key]["config"]["obs_space"],
                                            arena_exps[arena_exp_key]["config"]["act_space"],
                                            dcopy(policy_config),
                                        )

                                    # policy_mapping_fn: a map from agent_id to policy_id
                                    # use policy_mapping_fn_i2i as policy_mapping_fn
                                    arena_exps[arena_exp_key]["config"]["multiagent"]["policy_mapping_fn"] = ray.tune.function(
                                        policy_mapping_fn_i2i
                                    )

                                    arena_exps[arena_exp_key]["config"]["callbacks"] = {
                                    }
                                    arena_exps[arena_exp_key]["config"]["callbacks"]["on_train_result"] = ray.tune.function(
                                        on_train_result
                                    )

                                    arena_exps[arena_exp_key]["config"]["multiagent"]["policies_to_train"] = dcopy(
                                        arena_exps[arena_exp_key]["config"]["learning_policy_ids"]
                                    )

    return arena_exps
