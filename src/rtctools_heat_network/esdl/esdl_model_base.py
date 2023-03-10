import logging

from esdl import InPort


from rtctools_heat_network.pycml import Model as _Model

logger = logging.getLogger("rtctools_heat_network")


RETRY_LOOP_LIMIT = 100


class _RetryLaterException(Exception):
    pass


class _SkipAssetException(Exception):
    pass


class _ESDLModelBase(_Model):
    def _esdl_convert(self, converter, assets, prefix):
        # Sometimes we need information of one component in order to convert
        # another. For example, the nominal discharg of a pipe is used to set
        # the nominal discharge of its connected components.
        retry_assets = list(assets.values())
        skip_assets = list()

        for _ in range(RETRY_LOOP_LIMIT):
            current_assets = retry_assets
            retry_assets = []

            for asset in current_assets:
                try:
                    pycml_type, modifiers = converter.convert(asset)
                except _SkipAssetException:
                    skip_assets.append(asset)
                    continue
                except _RetryLaterException:
                    retry_assets.append(asset)
                    continue

                self.add_variable(pycml_type, asset.name, **modifiers)

            if not retry_assets:
                break
        else:
            raise Exception("Parsing of assets exceeded maximum iteration limit.")

        in_suf = f"{prefix}In"
        out_suf = f"{prefix}Out"
        node_suf = f"{prefix}Conn"

        skip_asset_ids = {a.id for a in skip_assets}
        pipe_assets = [
            a for a in assets.values() if a.asset_type == "Pipe" and a.id not in skip_asset_ids
        ]
        node_assets = [
            a for a in assets.values() if a.asset_type == "Joint" and a.id not in skip_asset_ids
        ]
        non_node_assets = [
            a for a in assets.values() if a.asset_type != "Joint" and a.id not in skip_asset_ids
        ]

        # Here we check that every pipe and node has the correct coupled carrier for their _ret
        # asset.
        for asset in [*pipe_assets, *node_assets]:
            asset_carrier = asset.global_properties["carriers"][asset.in_ports[0].carrier.id]
            couple_asset_carrier = next(
                x.global_properties["carriers"][x.in_ports[0].carrier.id]
                for x in [*pipe_assets, *node_assets]
                if (
                    x.name.replace("_ret", "") == asset.name.replace("_ret", "")
                    and x.name != asset.name
                )
            )
            if asset_carrier["name"] != couple_asset_carrier["name"]:
                raise Exception(
                    f"{asset.name} and {asset.name}_ret do not have the matching "
                    f"carriers specified"
                )

        # First we map all port ids to their respective PyCML ports. We only
        # do this for non-nodes, as for nodes we don't quite know what port
        # index a connection has to use yet.
        port_map = {}

        for asset in non_node_assets:
            component = getattr(self, asset.name)
            # We assume that every component has 2 ports. Essentially meaning that we are dealing
            # with a single commodity for a component. Exceptions, assets that deal with multiple
            # have to be specifically specified what port configuration is expected in the model.
            if asset.asset_type == "GenericConversion" or asset.asset_type == "HeatPump":
                if prefix != "Heat":
                    raise Exception(
                        "Hydraulically decoulpled systems are not yet supported for nonlinear (QTH)"
                        "optimization"
                    )
                # check for expected number of ports
                if len(asset.in_ports) == 2 and len(asset.out_ports) == 2:
                    for p in [*asset.in_ports, *asset.out_ports]:
                        if isinstance(p, InPort):
                            if "_ret" in p.carrier.name:
                                port_map[p.id] = getattr(component.Secondary, in_suf)
                            else:
                                port_map[p.id] = getattr(component.Primary, in_suf)
                        else:  # OutPort
                            if "_ret" in p.carrier.name:
                                port_map[p.id] = getattr(component.Primary, out_suf)
                            else:
                                port_map[p.id] = getattr(component.Secondary, out_suf)
                else:
                    raise Exception(f"{asset.name} has does not have 2 in_ports and 2 out_ports")
            elif len(asset.in_ports) == 1 and len(asset.out_ports) == 1:
                port_map[asset.in_ports[0].id] = getattr(component, in_suf)
                port_map[asset.out_ports[0].id] = getattr(component, out_suf)
            else:
                raise Exception(f"Unsupported ports for asset type {asset.name}.")

        # Nodes are special in that their in/out ports can have multiple
        # connections. This means we have some bookkeeping to do per node. We
        # therefore do the nodes first, and do all remaining connections
        # after.
        connections = set()

        for asset in node_assets:
            component = getattr(self, asset.name)

            i = 1
            if len(asset.in_ports) != 1 or len(asset.out_ports) != 1:
                Exception(
                    f"{asset.name} has !=1 in or out ports, please only use one, "
                    f"multiple connections to a single joint port are allowed"
                )
            for port in (asset.in_ports[0], asset.out_ports[0]):
                for connected_to in port.connectedTo.items:
                    conn = (port.id, connected_to.id)
                    if conn in connections or tuple(reversed(conn)) in connections:
                        continue

                    self.connect(getattr(component, node_suf)[i], port_map[connected_to.id])
                    connections.add(conn)
                    i += 1

        skip_port_ids = set()
        for a in skip_assets:
            if a.in_ports is not None:
                for port in a.in_ports:
                    skip_port_ids.add(port.id)
            if a.out_ports is not None:
                for port in a.out_ports:
                    skip_port_ids.add(port.id)

        # All non-Joints/nodes
        for asset in non_node_assets:
            for port in [*asset.in_ports, *asset.out_ports]:
                connected_ports = [p for p in port.connectedTo.items if p.id not in skip_port_ids]
                if len(connected_ports) != 1:
                    logger.warning(
                        f"{asset.asset_type} '{asset.name}' has multiple connections"
                        f" to a single port. "
                    )

                assert len(connected_ports) == 1

                for connected_to in connected_ports:
                    conn = (port.id, connected_to.id)
                    if conn in connections or tuple(reversed(conn)) in connections:
                        continue

                    self.connect(port_map[port.id], port_map[connected_to.id])
                    connections.add(conn)
