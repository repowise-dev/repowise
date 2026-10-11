add_library(coffee SHARED src/brew.cc)
target_include_directories(coffee PUBLIC include)
target_compile_definitions(coffee PRIVATE COFFEE_EXPORT_BUILD)
