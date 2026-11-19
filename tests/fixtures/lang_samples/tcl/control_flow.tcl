# Synthetic Tcl sample for parser and complexity goldens.
package require Tcl 8.6
source [file dirname [info script]]/helpers.tcl

namespace eval ::sample {
    proc ::sample::classify {kind value} {
        if {$kind eq "int" && $value > 0} {
            return positive
        } elseif {$kind eq "int"} {
            return other
        } else {
            return [expr {$value ne "" ? "text" : "empty"}]
        }
    }

    proc dispatch {mode items} {
        switch -exact -- $mode {
            fast -
            quick { return [lmap item $items {format_item $item}] }
            slow {
                foreach item $items {
                    if {[catch {format_item $item} err]} {
                        puts stderr $err
                    }
                }
            }
            default { error "unknown mode $mode" }
        }
    }
}

proc loops {limit} {
    for {set i 0} {$i < $limit} {incr i} {
        ::sample::classify int $i
    }
    while {$limit > 0 || [info exists ::force]} {
        incr limit -1
    }
}
