#pragma once

#include <gtest/gtest.h>

#include "coffee/brew.h"

class BrewFixture : public ::testing::Test {
 protected:
  coffee::Brewer brewer;
};
