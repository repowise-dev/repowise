/* Functions reached only through driver callbacks, closures and applies. */

int *levels;

int do_wave(string arg) { return 1; }
void tick() { }
void got_name(string str) { }
int by_level(int a, int b) { return a > b; }
int alive(object ob) { return 1; }
void later() { }
int closure_fn(int x) { return x; }
int id(string str) { return str == "thing"; }          /* apply: the driver calls it */
void lonely() { }                                       /* no syntactic caller */
void notmine() { }                                      /* named on another object */

void init()
{
    add_action("do_wave", "wave");
    call_out("tick", 2);
    input_to("got_name");
    levels = sort_array(levels, "by_level");
    filter_array(({ }), "alive", this_object());
    call_other(this_object(), "later");
    call_other(environment(), "notmine");
    map(({ 1 }), #'closure_fn);
    map(({ 1 }), #'tell_object);
}
