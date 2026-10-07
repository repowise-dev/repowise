<?php
// Control-flow fixture for the PHP complexity walker.

function deeply_nested($x) {
    if ($x > 0) {
        while ($x > 0) {
            if ($x > 1) {
                if ($x > 2) {
                    echo $x;
                }
            }
            $x--;
        }
    }
}

function many_branches($a, $b) {
    if ($a === null) {
        return 0;
    }
    if ($a > 0 && $b > 0) {
        return 1;
    } elseif ($a > 0 || $b > 0) {
        return 2;
    } else if ($a === 0) {
        return 3;
    } else {
        return 4;
    }
}

function wordy($a, $b) {
    return ($a and $b) or $a;
}

function shallow($x) {
    return $x;
}

function lambda_and_arrow($items) {
    $anon = function($x) {
        if ($x > 0) {
            return $x * 2;
        }
        return 0;
    };
    $arrow = fn($x) => $x > 0 ? $x * 2 : 0;
    return array_map($arrow, array_map($anon, $items));
}

function match_and_switch($x) {
    switch ($x) {
        case 1:
            $res = 'one';
            break;
        case 2:
            $res = 'two';
            break;
        default:
            $res = 'other';
            break;
    }

    $matched = match ($x) {
        1 => 'one',
        2, 3 => 'two_or_three',
        default => 'other',
    };

    return $res . $matched;
}

function try_catch_finally($path) {
    try {
        $data = file_get_contents($path);
        if ($data === false) {
            throw new Exception("read failed");
        }
    } catch (InvalidArgumentException $e) {
        echo "invalid: " . $e->getMessage();
    } catch (Exception $e) {
        echo "error: " . $e->getMessage();
    } finally {
        echo "cleanup";
    }
}

function loops_galore($items) {
    $count = 0;
    for ($i = 0; $i < count($items); $i++) {
        $count += $items[$i];
    }
    foreach ($items as $k => $v) {
        $count += $v;
    }
    do {
        $count--;
    } while ($count > 100);
    return $count;
}
