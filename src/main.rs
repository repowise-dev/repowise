fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.contains(&"--global".to_string()) {
        let home_dir = std::env::var("HOME").unwrap_or(".".to_string());
        let global_path = format!("{}/.repowise", home_dir);
        std::fs::create_dir_all(&global_path).unwrap();
        // Rest of the code to generate insights and store them in global_path
    } else {
        // Existing code to update files in the repository
    }
}