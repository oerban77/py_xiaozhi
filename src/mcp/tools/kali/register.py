"""Kali Linux security tools — tool registration."""

from __future__ import annotations

from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import (
    nm_basic_symbols,
    nm_demangle_symbols,
    nm_dynamic_symbols,
    nm_numeric_sort,
    nm_size_sort,
    nm_undefined_symbols,
    nmap_basic_scan,
    nmap_intense_scan,
    nmap_quick_scan,
    nmap_stealth_scan,
    nmap_vulnerability_scan,
    objdump_disassemble,
    objdump_file_headers,
    objdump_full_contents,
    objdump_section_headers,
    objdump_symbol_table,
    strings_basic,
    strings_encoding,
    strings_min_length,
    strings_offset,
    traceroute,
    tshark_analyze_pcap,
    tshark_capture_live,
    tshark_conversation_statistics,
    tshark_expert_info,
    tshark_extract_http,
    tshark_protocol_hierarchy,
)

logger = get_logger()


def register_kali_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the Kali Linux security and analysis tools."""

    # ── nmap ────────────────────────────────────────────────────────────────
    for name, desc, callback in (
        (
            "nmap_basic_scan",
            "Fast local network scan of the 100 most common ports, without reverse DNS.",
            nmap_basic_scan,
        ),
        (
            "nmap_intense_scan",
            "Intense scan: OS detection, version detection, script scanning and "
            "traceroute (nmap -T4 -A <target>).",
            nmap_intense_scan,
        ),
        (
            "nmap_stealth_scan",
            "TCP SYN half-open stealth scan (nmap -sS <target>). Requires "
            "root/administrator privileges.",
            nmap_stealth_scan,
        ),
        (
            "nmap_quick_scan",
            "Fast scan of the 100 most common ports (nmap -T4 -F <target>).",
            nmap_quick_scan,
        ),
        (
            "nmap_vulnerability_scan",
            "Service version detection plus the vulnerability NSE scripts "
            "(nmap -sV --script vuln <target>).",
            nmap_vulnerability_scan,
        ),
    ):
        add_tool(
            McpTool(
                name,
                desc,
                PropertyList([Property("target", PropertyType.STRING)]),
                callback,
            )
        )

    # ── nm ──────────────────────────────────────────────────────────────────
    for name, desc, callback in (
        (
            "nm_basic_symbols",
            "List the symbols of a binary or object file (nm <file>).",
            nm_basic_symbols,
        ),
        (
            "nm_dynamic_symbols",
            "List only the dynamic symbols (nm -D <file>).",
            nm_dynamic_symbols,
        ),
        (
            "nm_demangle_symbols",
            "Demangle C++ symbol names (nm -C <file>).",
            nm_demangle_symbols,
        ),
        (
            "nm_numeric_sort",
            "List the symbols sorted by address (nm -n <file>).",
            nm_numeric_sort,
        ),
        (
            "nm_size_sort",
            "List the symbols sorted by size (nm -S <file>).",
            nm_size_sort,
        ),
        (
            "nm_undefined_symbols",
            "List only the undefined symbols (nm -u <file>).",
            nm_undefined_symbols,
        ),
    ):
        add_tool(
            McpTool(
                name,
                desc,
                PropertyList([Property("target", PropertyType.STRING)]),
                callback,
            )
        )

    # ── objdump ─────────────────────────────────────────────────────────────
    add_tool(
        McpTool(
            "objdump_file_headers",
            "Show the file headers of a binary (objdump -f <file>).",
            PropertyList([Property("target", PropertyType.STRING)]),
            objdump_file_headers,
        )
    )
    add_tool(
        McpTool(
            "objdump_disassemble",
            "Disassemble a section of a binary (objdump -d -j <section> <file>).",
            PropertyList(
                [
                    Property("target", PropertyType.STRING),
                    Property("section", PropertyType.STRING, ".text"),
                ]
            ),
            objdump_disassemble,
        )
    )
    add_tool(
        McpTool(
            "objdump_symbol_table",
            "Show the symbol table (objdump -t <file>).",
            PropertyList([Property("target", PropertyType.STRING)]),
            objdump_symbol_table,
        )
    )
    add_tool(
        McpTool(
            "objdump_section_headers",
            "Show the section headers (objdump -h <file>).",
            PropertyList([Property("target", PropertyType.STRING)]),
            objdump_section_headers,
        )
    )
    add_tool(
        McpTool(
            "objdump_full_contents",
            "Show all headers, relocations and contents (objdump -x <file>).",
            PropertyList([Property("target", PropertyType.STRING)]),
            objdump_full_contents,
        )
    )

    # ── strings ─────────────────────────────────────────────────────────────
    add_tool(
        McpTool(
            "strings_basic",
            "Extract printable strings from a file (strings <file>).",
            PropertyList([Property("target", PropertyType.STRING)]),
            strings_basic,
        )
    )
    add_tool(
        McpTool(
            "strings_min_length",
            "Extract printable strings of at least N characters "
            "(strings -n <length> <file>).",
            PropertyList(
                [
                    Property("target", PropertyType.STRING),
                    Property("length", PropertyType.INTEGER, 6, 1, 4096),
                ]
            ),
            strings_min_length,
        )
    )
    add_tool(
        McpTool(
            "strings_offset",
            "Extract strings together with their offset in the file "
            "(strings -t <format> <file>).",
            PropertyList(
                [
                    Property("target", PropertyType.STRING),
                    Property("format", PropertyType.STRING, "x"),
                ]
            ),
            strings_offset,
        )
    )
    add_tool(
        McpTool(
            "strings_encoding",
            "Extract strings in a specific character encoding "
            "(strings -e <encoding> <file>).",
            PropertyList(
                [
                    Property("target", PropertyType.STRING),
                    Property("encoding", PropertyType.STRING, "S"),
                ]
            ),
            strings_encoding,
        )
    )

    # ── tshark ──────────────────────────────────────────────────────────────
    add_tool(
        McpTool(
            "tshark_capture_live",
            "Capture live network traffic on an interface for a number of "
            "seconds (tshark -i <iface> -a duration:N).",
            PropertyList(
                [
                    Property("interface", PropertyType.STRING),
                    Property("duration", PropertyType.INTEGER, 30, 1, 300),
                    Property("filter", PropertyType.STRING, ""),
                ]
            ),
            tshark_capture_live,
        )
    )
    add_tool(
        McpTool(
            "tshark_analyze_pcap",
            "Read a capture file, optionally filtered with a Wireshark display "
            "filter (tshark -r <file> -Y <filter>).",
            PropertyList(
                [
                    Property("pcap_file", PropertyType.STRING),
                    Property("display_filter", PropertyType.STRING, ""),
                ]
            ),
            tshark_analyze_pcap,
        )
    )
    for name, desc, callback in (
        (
            "tshark_extract_http",
            "Extract HTTP request methods and URIs from a capture file.",
            tshark_extract_http,
        ),
        (
            "tshark_protocol_hierarchy",
            "Show the protocol hierarchy statistics of a capture file.",
            tshark_protocol_hierarchy,
        ),
        (
            "tshark_conversation_statistics",
            "Show the IP conversation statistics of a capture file.",
            tshark_conversation_statistics,
        ),
        (
            "tshark_expert_info",
            "Show the expert info (errors, warnings, notes) of a capture file.",
            tshark_expert_info,
        ),
    ):
        add_tool(
            McpTool(
                name,
                desc,
                PropertyList([Property("pcap_file", PropertyType.STRING)]),
                callback,
            )
        )

    # ── traceroute ──────────────────────────────────────────────────────────
    add_tool(
        McpTool(
            "traceroute",
            "Trace the network route to a host (traceroute on Linux/macOS, "
            "tracert on Windows).",
            PropertyList([Property("target", PropertyType.STRING)]),
            traceroute,
        )
    )

    logger.info("Kali tools registered")
