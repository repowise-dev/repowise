#include "../sys/paths.h"
#include <ansi.h>
inherit "/std/room";
private int counter;
protected int helper(int value) { return value + counter; }
int proto();
struct point { int x; int y; };
void create() { helper(1); }
