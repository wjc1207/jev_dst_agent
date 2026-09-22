name = "JEV DST Agent"
description = [[
Exports the local player's structured state to client_log.txt and accepts
semantic actions from the local JEV controller.
]]
author = "Local JEV Agent"
version = "1.0.2"

api_version = 10
dst_compatible = true
client_only_mod = true
all_clients_require_mod = false

server_filter_tags = {}

configuration_options =
{
    {
        name = "sample_interval",
        label = "Sample interval",
        hover = "Seconds between state records written to client_log.txt.",
        options =
        {
            { description = "0.5 seconds", data = 0.5 },
            { description = "1 second", data = 1.0 },
            { description = "2 seconds", data = 2.0 },
        },
        default = 1.0,
    },
    {
        name = "scan_radius",
        label = "Nearby scan radius",
        hover = "World units searched for useful resources, light and danger.",
        options =
        {
            { description = "8", data = 8 },
            { description = "12", data = 12 },
            { description = "16", data = 16 },
        },
        default = 12,
    },
}
