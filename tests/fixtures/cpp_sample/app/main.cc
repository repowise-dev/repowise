#include "coffee/brew.h"

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;
  coffee::Brewer brewer;
  coffee::Beans beans{"ethiopia", 18};
  coffee::Cup cup = brewer.brew(beans);
  return cup.milliliters > 0 ? 0 : 1;
}
