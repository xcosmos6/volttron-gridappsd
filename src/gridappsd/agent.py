"""
Agent documentation goes here.
For a quick tutorial on Agent Development, see https://volttron.readthedocs.io/en/develop/developing-volttron/developing-agents/agent-development.html#agent-development
"""

import logging
import sys
from pprint import pformat
import datetime

from gridappsd import GridAPPSD

from volttron import utils
from volttron.utils.commands import vip_main
from volttron.client.messaging.health import STATUS_GOOD
from volttron.client.vip.agent import Agent, Core, PubSub, RPC
from volttron.client.vip.agent.subsystems.query import Query
from volttron.client.messaging import headers as headers_mod

import gevent
import stomp
import yaml
from gridappsd import topics as t
from gridappsd_integration import GridAPPSDSimIntegration

# from . import __version__
__version__ = "0.1.0"

# Setup logging so that it runs within the platform
utils.setup_logging()

# The logger for this agent is _log and can be used throughout this file.
_log = logging.getLogger(__name__)


def gridappsd(config_path, **kwargs):
    """
    Parses the Agent configuration and returns an instance of
    the agent created using that configuration.

    :param config_path: Path to a configuration file.
    :type config_path: str
    :returns: Gridappsd
    :rtype: Gridappsd
    """
    try:
        config = utils.load_config(config_path)
    except Exception:
        config = {}

    if not config:
        _log.info("Using Agent defaults for starting configuration.")

    # setting1 = int(config.get('setting1', 1))
    # setting2 = config.get('setting2', "some/random/topic")

    return Gridappsd(config, **kwargs)


class Gridappsd(Agent):
    """
    Gridappsd is an example file that listens to the message bus and prints it to the log.
    """

    def __init__(self, config, **kwargs):
        super(Gridappsd, self).__init__(enable_store=False, **kwargs)
        _log.debug("vip_identity: " + self.core.identity)
        self.config = config
        self.Gridappsd_sim = GridAPPSDSimIntegration(config, self.vip.pubsub)
        self.volttron_subscriptions = None
        self.sim_complete = False
        self.rcvd_measurement = False
        self.rcvd_first_measurement = 0
        self.are_we_paused = False
        self.sim_complete = True
        self.sim_running = False

        # self.setting1 = setting1
        # self.setting2 = setting2
        # # Runtime limit allows the agent to stop automatically after a specified number of seconds.
        # self.runtime_limit = 0

        # self.default_config = {"setting1": setting1,
        #                        "setting2": setting2}
        # # Set a default configuration to ensure that self.configure is called immediately to setup
        # # the agent. need to set enable_store to be True above.
        # self.vip.config.set_default("config", self.config)
        # # Hook self.configure up to changes to the configuration file "config".
        # self.vip.config.subscribe(self.configure, actions=["NEW", "UPDATE"], pattern="config")

    def configure(self, config_name, action, contents):
        """
        Called after the Agent has connected to the message bus. If a configuration exists at startup
        this will be called before onstart.

        Is called every time the configuration in the store changes.
        """
        config = self.config.copy()
        config.update(contents)

        _log.debug("Configuring Agent")

        try:
            setting1 = int(config["setting1"])
            setting2 = str(config["setting2"])
        except ValueError as e:
            _log.error("ERROR PROCESSING CONFIGURATION: {}".format(e))
            return

        self.setting1 = setting1
        self.setting2 = setting2
        self.runtime_limit = int(config.get('runtime_limit', 0))

        # self._set_runtime_limit()
        self._create_subscriptions(self.setting2)

    def _set_runtime_limit(self):
        if not self.runtime_limit and self.runtime_limit > 0:
            stop_time = datetime.datetime.now() + datetime.timedelta(seconds=self.runtime_limit)
            _log.info('Gridappsd agent will stop at {}'.format(stop_time))
            self.core.schedule(stop_time, self.core.stop)
        else:
            _log.info('No valid runtime_limit configured; Gridappsd agent will run until manually stopped')

    def _create_subscriptions(self, topic):
        """
        Unsubscribe from all pub/sub topics and create a subscription to a topic in the configuration which triggers
        the _handle_publish callback
        """
        self.vip.pubsub.unsubscribe("pubsub", None, None)

        self.vip.pubsub.subscribe(peer='pubsub',
                                  prefix=topic,
                                  callback=self._handle_publish)

    def _handle_publish(self, peer, sender, bus, topic, headers, message):
        """
        Callback triggered by the subscription setup using the topic from the agent's config file
        """
        _log.info("received publication from {0} with topic {1}.".format(sender, topic))
        self.vip.pubsub.publish('pubsub', "gridappsd/status", message="received publication from {0} with topic {1}.".format(sender, topic))

    @Core.receiver("onstart")
    def onstart(self, sender, **kwargs):
        """
        This is method is called once the Agent has successfully connected to the platform.
        This is a good place to setup subscriptions if they are not dynamic or
        do any other startup activities that require a connection to the message bus.
        Called after any configurations methods that are called at startup.

        Usually not needed if using the configuration store.
        """
        # Example publish to pubsub
        # self.vip.pubsub.publish('pubsub', "some/random/topic", message="HI!")

        # Example RPC call
        # self.vip.rpc.call("some_agent", "some_method", arg1, arg2)
                # subscribe to the VOLTTRON topics if given.
        if self.volttron_subscriptions is not None:
            for sub in self.volttron_subscriptions:
                _log.info('Subscribing to {}'.format(sub))
                self.vip.pubsub.subscribe(peer='pubsub',
                                          prefix=sub,
                                          callback=self.on_receive_publisher_message)

        # Exit if GridAPPSD isn't installed in the current environment.
        if not self.Gridappsd_sim.is_sim_installed():
            _log.error("GridAPPSD module is unavailable please add it to the python environment.")
            self.core.stop()
            return

        try:
            # Register events with GridAPPSD
            # There are 4 event callbacks that GridAPPSD provides to monitor the status
            # - onstart, ontimestep, onmesurement, oncomplete
            # This example shows how to register with GridAPPSD simulation to get
            # event notifications
            event_callbacks = {'MEASUREMENT': self.onmeasurement,
                               "TIMESTEP": self.ontimestep,
                               "FINISH": self.onfinishsimulation}
            self.Gridappsd_sim.register_event_callbacks(event_callbacks)

            # Register the config file with GridAPPS-D
            self.Gridappsd_sim.register_inputs(self.config, self.do_work)
            # Start the simulation
            self.Gridappsd_sim.start_simulation()

            # Waiting for simulation to start
            i = 1
            while not self.Gridappsd_sim.sim_id and i < 20:
                _log.debug(f"waiting for simulation to start {i}")
                gevent.sleep(1)
                i += 1

            # Subscribe to GridAPPSD log messages
            if self.Gridappsd_sim.sim_id:
                self.Gridappsd_sim.gridappsd.subscribe(
                    t.simulation_log_topic(self.Gridappsd_sim.sim_id),
                    self.on_message)
                self.sim_running = True
            else:
                self.sim_running = False
                _log.debug("Simulation did not start")
        except stomp.exception.NotConnectedException as ex:
            _log.error("Unable to connect to GridAPPSD: {}".format(ex))
            _log.error("Exiting!!")
            self.core.stop()
        except ValueError as ex:
            _log.error("Unable to register inputs with GridAPPSD: {}".format(ex))
            self.core.stop()
            return

    @Core.receiver("onstop")
    def onstop(self, sender, **kwargs):
        """
        This method is called when the Agent is about to shutdown, but before it disconnects from
        the message bus.
        """
        _log.info("received stop from {0}.".format(sender))
        self.vip.pubsub.publish('pubsub', "gridappsd/status", message="received stop from {0}.".format(sender))

        if self.sim_running:
            self.Gridappsd_sim.stop_simulation()

    @RPC.export
    def rpc_method(self, arg1, arg2, kwarg1=None, kwarg2=None):
        """
        RPC method

        May be called from another agent via self.vip.rpc.call
        """
        return self.setting1 + arg1 - arg2

    # @PubSub.subscribe('pubsub', '', all_platforms=True)
    # def on_match(self, peer, sender, bus, topic, headers, message):
    #     """Use match_all to receive all messages and print them out."""
    #     _log.info(
    #         "Peer: {0}, Sender: {1}:, Bus: {2}, Topic: {3}, Headers: {4}, "
    #         "Message: \n{5}".format(peer, sender, bus, topic, headers, pformat(message)))

    def on_message(self, headers, message):
        """
        Callback method to receive GridAPPSD log messages
        :param headers: headers
        :param message: log message
        :return:
        """
        # self.vip.pubsub.publish('pubsub', "gridappsd/status", message="on message: {0}.".format(message))

        try:
            _log.debug("Received GridAPPSD log message: {}".format(message))
            json_msg = yaml.safe_load(str(message))

            if "PAUSED" in json_msg["processStatus"]:
                _log.debug("GridAPPSD simulation has paused!!")

            if "resume" in json_msg["logMessage"]:
                _log.debug("GridAPPSD simulation has resumed!!")

        except Exception as e:
            message_str = "An error occurred while trying to translate the  message received" + str(e)

    def onmeasurement(self, sim, timestep, measurements):
        """
        Callback method to receive measurements
        :param sim: simulation object
        :param timestep: time step
        :param measurements: measurement value
        :return:
        """
        # _log.info('Measurement received at %s', timestep)
        # self.vip.pubsub.publish('pubsub', "gridappsd/status", message="Measurement received at {0}.".format(timestep))

        if not self.are_we_paused and not self.rcvd_first_measurement:
            _log.debug("Pausing sim now")
            self.Gridappsd_sim.pause_simulation()
            self.are_we_paused = True
            _log.debug(f"ARWEPAUSED {self.are_we_paused}")
            # Setting up so if we get another measurement while we
            # are paused we know it
            self.rcvd_measurement = False
            # Resume simulation after 30 sec
            self.core.spawn_later(30, self.resume_gridappsd_simulation)

        if not self.rcvd_measurement:
            print(f"A measurement {measurements} happened at {timestep}")
            data = {"data": measurements}

            headers = {
                headers_mod.DATE: utils.format_timestamp(utils.get_aware_utc_now()),
                headers_mod.CONTENT_TYPE: headers_mod.CONTENT_TYPE.JSON
            }
            # Publishing measurement on VOLTTRON message bus
            self.vip.pubsub.publish(peer='pubsub',
                                    topic='gridappsd/measurement',
                                    message=data,
                                    headers=headers)
            self.rcvd_measurement = True
        else:
            self.rcvd_measurement = True
        self.rcvd_first_measurement = True

    def ontimestep(self, sim, timestep):
        """
        Event callback for timestep change
        :param sim:
        :param timestep:
        :return:
        """
        # _log.debug("Timestamp: {}".format(timestep))
        # self.vip.pubsub.publish('pubsub', "gridappsd/status", message="on timestep at {0}.".format(timestep))

    def onfinishsimulation(self, sim):
        """
        Event callback to get notified when simulation has completed
        :param sim:
        :return:
        """
        self.sim_complete = True
        _log.info('Simulation Complete')
        self.vip.pubsub.publish('pubsub', "gridappsd/status", message="Simulation Complete.")

    def resume_gridappsd_simulation(self):
        """
        Resume simulation if paused
        :return:
        """
        if self.are_we_paused:
            _log.debug('Resuming simulation')
            self.Gridappsd_sim.resume_simulation()
            _log.debug('Resumed simulation')
            self.are_we_paused = False
    
    def on_receive_publisher_message(self, peer, sender, bus, topic, headers, message):
        """
        Subscribe to publisher publications and change the data accordingly
        """
        # Update controller data
        val = message[0]
        # _log.info("received publication from {0} with topic {1}.".format(sender, topic))
        self.vip.pubsub.publish('pubsub', "gridappsd/status", message="received publication from {0} with topic {1}.\nPeer: {2},\nBus: {3},\nMessage: {4}".format(sender, topic, peer, bus, message))
        # Do something with message

    def do_work(self):
        """
        Dummy callback for GridAPPS-D sim
        Unused
        :return:
        """
        pass

def main():
    """
    Main method called during startup of agent.
    :return:
    """
    try:
        vip_main(gridappsd, version=__version__)
    except Exception as e:
        _log.exception('unhandled exception')


if __name__ == '__main__':
    # Entry point for script
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
